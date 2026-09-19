# ADR-0034 — Conversation-level safety signals: detect the trajectory, not the turn

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** Principal Data Architect
- **Context tags:** AgentOps, responsible AI, vulnerable customers, fraud, security, controls
- **Related:** ADR-0008 (Model Armor), ADR-0015 (live evaluation), ADR-0022 (model pinning),
  ADR-0026 / docs/26 (controls alerting), `knowledge/playbooks/refusal-escalation.md`

## Context

Every safety control FinChat had before this ADR judged **one turn**:

| Control | Scope | Latency | Alerts on |
|---|---|---|---|
| Model Armor (ADR-0008) | one prompt or one response | inline | a block |
| Live LLM-as-judge (ADR-0015) | one sampled turn, 50/day | daily | nothing — scores feed a 7-day mean on a card |
| Offline CI gate | one scripted turn | per commit | a threshold on the fixed dataset |

None could see a conversation, because `conversation_log` had no conversation key: the SPA
sent `session_id` and `user_id`, the BFF dropped both, and every row got a fresh uuid.

That is the architecture described in *Raine v. OpenAI* (filed 2025-08-26). Per the
complaint, OpenAI's per-message moderation flagged hundreds of the user's messages for
self-harm content, some at over 90% confidence, across months of conversation. Nothing
aggregated those flags across the session or the person, nothing changed what the product
did, and no human was ever routed the thread. The failure was not a classifier miss; it was
the absence of any level above the message. OpenAI's own statement afterwards conceded that
safeguards "can sometimes become less reliable in long interactions".

For a bank the same shape shows up far more often as an **attacker** than as a customer in
crisis: five probes for another customer's data or the system prompt, each politely refused
by the agent (the playbook says refuse), each forgotten by the time the next arrives. Today
the Model Armor path makes this *worse* in one respect — its correlation key is per
principal per class, so the fifth attempt collapses into the same alert as the first (docs/26,
"Correlation keys on the detector class"), and every anonymous customer shares one
principal. Correct for flooding; blind to escalation. And the probes that do not trip Model
Armor at all — impersonation, "which tables do you query", a cross-customer reference — leave
no record anywhere.

Two further gaps: the judge's `safety` is a 0/1 averaged over a 7-day, 50/day sample, so
one catastrophic turn is 1/350 of a mean; and judge verdicts go nowhere but the Admin card.

## Decision

Add a level above the turn, decided **inside the request**, with three tiers that answer
two different questions.

### "Is *this* conversation going wrong?" — tiers 1 and 2, in the request

This is **detection against known patterns, not anomaly detection**. A customer in crisis
or an attacker probing is not a statistical deviation from the population; it is a
recognisable trajectory. The right tool is a classifier plus explicit, readable rules.

`ui/safety_signals.py`, on every customer turn of `/api/agent`:

1. **Classify prompt AND answer** against a closed taxonomy (Gemini Flash-Lite through the
   AI gateway, `workload_class=classification`; direct Vertex only when the gateway is
   unconfigured, never after a gateway refusal). Customer-side: `jailbreak_probe`,
   `identity_probe`, `system_probe`, `social_engineering`, `action_attempt` (security);
   `scam_victim`, `third_party_coercion` (fraud); `self_harm`, `financial_distress`,
   `gambling_harm` (wellbeing). Agent-side: `answer_policy_breach` — the answer leaked,
   complied, validated, coached, or advised — and `agent_refused`.
2. **Fold into the session state.** Prior `turn_signals` rows for the session (and the
   principal's 30-day counts) are read at the start of the request, overlapped with the
   agent call, so the state costs no latency. Model Armor blocks are folded in as turns.
3. **Decide**, with every threshold as data (`DEFAULT_THRESHOLDS`, `SAFETY_THRESHOLDS` to
   override):
   - **Tier 1 — the product acts, now.** `self_harm ≥ 0.7` → the answer is replaced with a
     crisis hand-off (988). `scam_victim`/`third_party_coercion ≥ 0.7` → fraud hand-off.
     Five security hits in a session, **or one security probe whose answer breached
     policy** → the session is **quarantined**: locked, every later turn answered with the
     lock message, regardless of content. CRITICAL for the leak; ERROR otherwise.
   - **Tier 2 — a human reviews.** Three wellbeing/fraud-flagged turns; three in a row;
     rising confidence over three; two refusals in a session with any signal (the
     playbook's "refused twice → human", finally enforced); three security hits, or three
     inside ten minutes; any `answer_policy_breach ≥ 0.7`; a principal with two flagged
     sessions in 30 days; a 40-turn session with any flag (long-context decay).
   - **Sticky.** After a tier-1 action the session is *supervised*: the acting threshold
     drops to 0.4 for the rest of it. "It was for a story" does not reset the counter.
     Quarantine is re-derived from the session's rows on every turn, so it survives
     instance restarts and scale-to-zero.
4. **Record every turn** to `finchat_eval_<env>.turn_signals` — names, confidences, tier,
   action, reasons, classifier model, and whether the turn went unscreened. No text: the FK
   to `conversation_log` reaches the words under the same dataset governance. This is the
   evidence plane in the docs/26 sense: every execution, so "was any turn unscreened" is a
   query.
5. **Emit** tier 1 and 2 decisions as ordinary control events, `source=conversation_safety`,
   `filters` = signal names only, correlated per principal **per session** per class. The
   Inc 26 chain carries them unchanged: stdout → Cloud Logging → sink → Pub/Sub → Eventarc →
   Workflows → ServiceNow `em_event` → incident, plus Chat. `session_quarantine`,
   `self_harm`, `scam_victim`, `third_party_coercion` and `answer_policy_breach` are added to
   the workflow's elevated set, so a tier-1 event in prod is severity 2.

**What "real time" means here.** The decision is made before the answer leaves the BFF:
one Flash-Lite call of added latency, the state read hidden behind the agent call. The
customer sees the hand-off or the lock on *that* turn. The human notification travels the
Inc 26 chain, which is seconds to about two minutes end to end. That is the honest figure:
the product reacts in the request; the person is paged in minutes.

### "Is the *system* going wrong?" — tier 3, on a window

This **is** anomaly detection, and its latency is its window by construction.
`scripts/safety_anomaly.py`, every 15 minutes (`safety-anomaly.yml`), compares the latest
complete bucket of `safety_buckets` — a 15-minute series of security rate, wellbeing rate,
refusal rate, unscreened rate, product actions, and distinct probing sessions, carrying
`model_served` — to the trailing 28-day baseline: a z-score for rates, a Poisson bound
(`x > mean + 3√mean`, floored) for counts, a 20-bucket minimum before anything fires. It
catches what no single conversation shows: a probing campaign across sessions, **refusals
collapsing after a silent model version change** (keyed per version, so a second change is
a second incident — ADR-0022's `model_served` is what makes it attributable), the
classifier itself being down. Anomalies leave as the same envelope, written through the
Logging API because a GitHub runner has no Cloud Run stdout; the sink's second branch (no
`service_name`, environment from payload) admits them.

### The offline gate learns about conversations

`eval/datasets/safety_trajectories.jsonl` holds ten scripted trajectories — the Raine
shape, the fiction reset, the coached transfer, the patient attacker, five Model Armor
blocks, a leak on the first probe, a burst, an agent that gave advice, a rude message that
is *not* a trajectory — with the expected tier at every turn. `evaluate.py` folds them
through the same `evaluate()`/`advance()` the BFF runs and gates at 1.0, and separately on
**late escalations = 0**: a trajectory that escalated a turn late is the Raine failure in
miniature. Verified to bite: loosening the lock to eight hits fails the build at 0.914.

## Rationale

- **Aggregate over the relationship, not the message.** Everything above the turn was
  missing; nothing at the turn was wrong. Model Armor and the judge stay exactly as they are.
- **Census for safety, sample for quality.** The judge keeps sampling 50/day for grounding
  and coherence. Safety screens every turn, because a tail event in a sample is a tail
  event you did not see.
- **Escalation must change what the product does.** A ticket alone would have been a ticket
  alone in the Raine case too. Tier 1 answers differently on the turn it fires and holds
  that state for the session.
- **Detection and anomaly detection are different tools.** Rules with thresholds a reviewer
  can read for the conversation; a baseline comparison for the population. Calling the
  first "anomaly detection" would have produced a model nobody could explain to a customer
  or a regulator; calling the second "rules" would have produced thresholds guessed from
  nothing.
- **Reuse the control chain.** One new `source` value; no new transport, workflow, or
  ServiceNow integration. The reconciliation control (docs/26) covers these events for free.
- **Names, never words.** The envelope still has no free-text field; the evidence row has
  no text column; the classifier is told not to quote. `test_safety_signals.py` pins all
  three with a leaked-SSN fixture.

## Consequences

- `conversation_log` gains `session_key`, `principal_hash`, `turn_index` (additive). New
  table `turn_signals`, views `session_trajectory` and `safety_buckets`
  (`scripts/eval_schema.sql`, apply with `bq query` per the runbook).
- BFF: `SAFETY_SIGNALS=1` enables; `SAFETY_FAIL_CLOSED=1` withholds the answer when the
  classifier cannot run (sandbox default is fail-open, with the unscreened turn recorded and
  counted by tier 3). `SAFETY_THRESHOLDS` overrides. Both flags are passed by
  `build-deploy.yml` from repository variables, off by default.
- Gateway: the classifier is agent `conversation_safety_classifier`, class `classification`
  (40k tokens/day by default). At ~1.5k tokens a turn that is under thirty screened turns a
  day before the budget refuses and turns go unscreened — raise `WORKLOAD_BUDGETS` on the
  gateway before enabling in prod, and watch `unscreened_rate`.
- CI/CD SA gains `roles/logging.logWriter` (foundation module) for the tier-3 runner.
- Cost: one Flash-Lite call and one small BigQuery read/write per customer turn; a
  15-minute scheduled Action. Enterprise mapping for the state read is the Bigtable hot
  path (ADR-0017); for tier 3 a Cloud Run Job on Scheduler, or a Monitoring alert on a
  log-based metric for the sub-minute, lossy variant.
- ServiceNow: the class travels in `message_key` (`…:<session>:wellbeing`), so the
  `em_alert_management_rule` can route wellbeing and fraud to the vulnerable-customer /
  fraud desk and security to infosec. That rule is configuration on the PDI, not code, and
  is not yet written; until it is, everything lands in the existing assignment group.
- Anonymous customers get session-level correlation only: `anonymous` is every signed-out
  customer, so cross-session recurrence needs the identity ADR-0016/0025 provide. The
  design does not pretend otherwise.

## Alternatives considered

- **Lower Model Armor's confidence threshold / add filters:** still per message. Would have
  raised the block rate and changed nothing about the fifth attempt.
- **Score every turn with the existing judge:** the judge sees no session either, runs
  daily, and scores in a batch a human never reads. Kept for quality; not the safety control.
- **A BigQuery scheduled query every 15 minutes for tiers 1–2:** near-line, cannot change
  the answer, and the first version of this design. Rejected once the requirement was stated
  as "minutes, and the product must react": only the request path can do the second.
- **A learned anomaly model over conversations:** unexplainable at the customer or the
  regulator; a distressed customer is not an outlier, they are a pattern. Used only where
  the question is genuinely populational (tier 3), and even there the simplest statistic
  that a reviewer can recompute.
- **Hard session caps to defeat long-context decay:** blunt and customer-hostile. The
  40-turn rule reviews rather than cuts, and tier 3's refusal-rate series measures the decay
  directly.
