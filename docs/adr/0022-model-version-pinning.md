# ADR-0022 — Model version pinning + scheduled canary evaluation

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Principal Data Architect
- **Context tags:** Model risk, drift, change control, AgentOps, supply chain

## Context

Every LLM call site in FinChat requested the floating alias `gemini-2.5-flash`. An alias
is a moving target: the provider repoints it, and the platform's behaviour changes with
no deploy, no PR, and no notification.

The best-documented illustration is published research measuring GPT-4's
prime-vs-composite identification accuracy falling from **84% to 51%** between March and
June 2023 on an *unchanged prompt*. A provider-side change nobody requested, nobody was
told about, and nobody could roll back cut task accuracy by a third. Against any model
risk obligation, that single result is the whole argument for version pinning.

Two things make this sharper for FinChat specifically:

1. **The regulatory framework does not cover it.** Revised guidance (SR 26-2 / OCC
   2026-13, April 2026) places generative AI outside model-risk scope, and SR 23-4
   (third-party risk) does not mention AI at all. Vendor foundation models have no
   purpose-built supervisory guidance, so version control over them is ours to impose.
2. **Neither existing eval loop can see drift.** `evaluate.py` is deterministic and never
   calls a model. `live_eval.py` judges real traffic, so questions change day to day and a
   quality drop is confounded with a change in what users asked. Drift would be invisible
   to both.

FINOS **AIR-PREV-010 (AI Model Version Pinning)** prescribes exactly this control and maps
it to NIST SP 800-53 CM-2/CM-3/CM-4/CM-8, AU-2, SA-4/SA-10/SA-22, SR-4/SR-8 — i.e. it
treats model versioning as configuration management plus supply-chain risk, which is the
right frame.

## Decision

Three parts, deliberately ordered by how much they actually prove.

### 1. Log the version that served — the half that is evidence

Vertex returns `modelVersion` on every `generateContent` response. FinChat records it per
turn in `conversation_log.model_served`, alongside `model_requested`.

These are **different facts**. Requesting a pin is an intention; recording what answered is
evidence. Where a surface does not report a version, the column stays `NULL` rather than
being back-filled from the request — back-filling would make the evidence a tautology and
is the single easiest way to turn this control into theatre.

This works today, on aliases, with no snapshot ids required.

### 2. Pin the requested version — declared, not assumed

`scripts/model_pins.py` is the source of truth: one entry per logical call site (`AGENT`,
`ROUTER`, `SEMANTICS`, `JUDGE`, `STEWARD`), each with an alias and an optional
`FINCHAT_PIN_<SITE>` override injected at deploy time.

**Snapshot ids are not set in code.** They must be confirmed against the live API for the
region in use and they change as versions retire; inventing one produces a config that
404s at runtime and a document that reads as verified when it is not.

> ### ⚠ Correction, 2026-08-05: for the model FinChat runs, there is nothing to pin
>
> Probed against `us-central1` (see [`scripts/check_pinnable.py`](../../scripts/check_pinnable.py)):
>
> | id form | result |
> |---|---|
> | `gemini-2.5-flash` | 200, `modelVersion: gemini-2.5-flash` |
> | `gemini-2.5-flash@default` | 200 |
> | `gemini-2.5-flash-001` | **404** |
> | `gemini-2.5-flash@001` | **404** |
>
> The publisher model resource reports `versionId: default` and the API echoes the alias
> back as the served version. The 1.5 and 2.0 generations shipped dated snapshots; 2.5
> currently does not. `@default` is **not** a pin — it is the alias with extra syntax, and
> it moves when `default` moves.
>
> This does not weaken the decision; it changes which half carries the weight. Part 3
> (the canary) is now the **primary** drift control here rather than the confirmation of
> part 2. Part 1 (logging what served) becomes a tripwire: the day Google starts returning
> a real version string, that change is visible in `conversation_log.model_served` and
> pinning becomes available.

Running an alias is a legitimate posture. Running one without having decided to is not.
`verify_agent_registry.py` reports **PIN-1** either way, but distinguishes the two:
a WARNING where a snapshot exists and we chose not to use it, and an INFO where none is
published. A warning nobody can clear is one people learn to ignore, and an ignored
warning channel stops carrying the ones that matter.

### 3. Canary the golden set on a schedule — the detection

`scripts/canary_eval.py` replays the **same golden set** against the **live agent** daily
and compares to a stored baseline. Because the input is fixed, a metric move is
attributable to the system rather than to traffic. Tolerances are absolute (10 points on
grounding and tool-use, 5 on hallucination) and set to catch a real move rather than a
wobble — a canary that fires on noise gets muted, and a muted control is worse than none.

Every run records the serving version. When a regression coincides with a version change,
the report says so explicitly, because *"quality fell"* is an alert and *"quality fell the
day the serving version changed from X to Y"* is a diagnosis.

This is the only drift technique with published evidence behind it. Embedding-drift
dashboards are investigative aids, not controls, and are deliberately not adopted here.

## Consequences

**Positive**

- Provider-side behaviour change becomes detectable, attributable, and dated.
- Every production turn carries the version that produced it — the prerequisite for any
  retrospective question about a past answer.
- Implements a named control from a catalogue authored by peer institutions rather than
  one invented here.
- The pinning posture of every call site is visible in each build.

**Negative / accepted**

- **The canary costs tokens daily.** Seven cases against one agent; small, but not zero,
  and it grows with the golden set.
- **A canary is only as good as its golden set.** Seven transaction cases will not detect
  a regression in a behaviour the set does not exercise. Coverage is the real limit here,
  and expanding it is the follow-up work.
- **Pins must be maintained.** A pinned snapshot eventually retires; an unmaintained pin
  fails closed at runtime. This trades silent behaviour change for loud availability
  failure, which is the correct trade for a bank but is a trade.
- **Right now there is no pin to maintain, and that is worse.** With no snapshot
  published for gemini-2.5-flash, the alias can be repointed at any time and *nothing in
  the request or the response reveals it* — the canary moving is the only signal. The
  nearest real version commitment on Vertex is **Provisioned Throughput**, which is worth
  raising with the account team; note it is an availability control that happens to pin,
  not a cost optimization.
- **`PINNABLE` is a recorded fact about someone else's product.** It was verified on a
  date and will go stale silently. `check_pinnable.py` exits non-zero when the live API
  contradicts it, so it can be scheduled; it is not scheduled today.
- **Managed surfaces cannot be pinned.** Conversational Analytics (M5) has its version
  governed by the service. The registry records this rather than pretending otherwise.
- **`model_served` depends on the provider reporting it.** No report, no evidence.

## Alternatives considered

- **Pin and skip the canary.** Rejected: a pin that is never verified is an assumption.
  The 84%→51% case is precisely one where the *requested* configuration was unchanged.
- **Embedding-drift monitoring.** Rejected as a control. Classical drift tooling assumes a
  fixed model; with a third-party LLM the model itself is the moving dependency, and no
  published evidence supports these dashboards as detection. Useful for investigation.
- **Rely on provider deprecation notices.** Insufficient: alias repointing is not a
  deprecation event and generally carries no notice.
- **Use the live-eval judge as the drift signal.** Rejected: traffic varies, so the signal
  is confounded. Worse, the judge is itself a model from the same family — it can drift in
  the same direction as what it measures, which is why its own serving version is now
  recorded per score.

## References

- [19 — Model & Agent Inventory](../19-model-inventory.md) — control framework mapping
- [20 — Agent Registry & Identity](../20-agent-registry.md) — PIN-1 reporting
- [ADR-0015 — Live evaluation](0015-live-evaluation.md) — the complementary loop
- [ADR-0023 — Agent registry and identity](0023-agent-registry-and-identity.md)
- FINOS AI Governance Framework v2, AIR-PREV-010 (CC-BY-4.0)
