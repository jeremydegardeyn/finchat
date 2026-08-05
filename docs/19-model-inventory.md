# 19 — Model & Agent Inventory (FinChat AI Control Framework)

> Central register of every model and agent in FinChat: what it is, what it's used for,
> how risky it is, and how it's validated and monitored. This doc is the index;
> validation/monitoring detail lives in the linked ADRs and [`eval/README.md`](../eval/README.md).
> Data-level controls (classification, lineage, retention) are in
> [09 — Data Governance](09-data-governance.md).
>
> **Regulatory position as of 2026-08-04.** See [Two governance regimes](#two-governance-regimes)
> — this inventory deliberately spans models that remain in supervisory model-risk scope and
> models that no longer do.

## Why this doc exists

Historically this inventory was written to **SR 11-7 / OCC Bulletin 2011-12**, which expected
a firm-wide model inventory: a single place enumerating every model in use, its owner, risk
tier, and validation status.

**That guidance was rescinded on 17 April 2026.** The Federal Reserve, OCC and FDIC jointly
issued revised model risk management guidance — **SR 26-2 / OCC Bulletin 2026-13 /
FDIC FIL-15-2026** — which withdraws SR 11-7 (2011), SR 21-8, OCC Bulletin 2011-12, and the
OCC Model Risk Management handbook booklet. The operative sentence, from OCC Bulletin 2026-13:

> "Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not
> within the scope of this guidance."

Three consequences shape this document:

1. **The framework most institutions planned to extend over LLMs has been explicitly withdrawn
   from that role.** "We'll apply SR 11-7 to the agent" is no longer available as an answer.
2. **The replacement does not exist yet.** The agencies have signalled a Request for Information
   on AI, including generative and agentic AI; no date was announced and none had been published
   as of this document's date. Agentic AI remains subject to general safety-and-soundness
   expectations with no dedicated framework.
3. **The revised guidance is explicitly non-enforceable** — "non-compliance with this guidance
   will not result in supervisory criticism" — and applies primarily to banking organizations
   above $30B in assets.

So the inventory is no longer a compliance artifact for one regime. It is **FinChat's own
control framework**, designed to be defensible in the interim and to survive whatever the RFI
produces. The evidence FinChat generates — offline CI eval gates, live LLM-judge scoring,
factor attribution, I/O screening, immutable audit — did not change. What changed is that it
is now offered as a framework of our own design rather than as conformance to a rescinded one.

> ⚠ **Verify before citing externally.** SR 26-2 / OCC 2026-13 status and RFI progress should
> be confirmed against primary sources at time of use; this section is dated deliberately.

## Two governance regimes

The rescission splits this inventory rather than exempting it. A deterministic credit scorecard
is still a model in the supervisory sense; a tool-calling LLM agent explicitly is not.

| Regime | What falls here | Governing expectation |
|---|---|---|
| **A — Supervisory MRM scope** | Traditional quantitative models: deterministic scorecards, statistical estimators | Revised MRM guidance (SR 26-2 / OCC 2026-13), plus product-specific obligations — ECOA/Reg B adverse-action for credit |
| **B — Outside MRM scope** | Generative and agentic AI: LLM agents, LLM evaluators, NL→SQL, retrieval | **No dedicated supervisory framework.** Governed by FinChat's control framework below, general safety-and-soundness expectations, and the FINOS AI Governance Framework as the control catalogue |

Regime B is the larger and less settled half — six of FinChat's seven registered models. That
asymmetry is the point: the models carrying the most novel risk are the ones with the least
supervisory guidance behind them.

## Inventory

| # | Model | Regime | Type | Purpose | Owner | Risk tier | Validation | Monitoring | Ref |
|---|-------|:-:|------|---------|-------|:-:|-----------|------------|-----|
| M1 | Loan risk scorecard (`risk.py`, `MODEL_VERSION = risk-1.0.0`) | **A** | Traditional — deterministic additive scorecard | Credit decisioning (approve/refer/deny) for personal loans | Loans product team | **High** — direct credit decision, ECOA/Reg B adverse-action exposure | Offline eval vs. labeled applicant profiles (`loan_eval.jsonl`), approval-rec accuracy ≥ 0.80 gate; factor points sum-to-score checked in unit tests | Re-run on every CI change; `risk_assessment` + `loan_audit_log` immutable, 10y retention | [ADR-0013](adr/0013-loan-decision-explainability.md), [eval/README](../eval/README.md) |
| M2 | Banking Assistant agent (Gemini 2.5 Flash via Vertex, `products/transactions/agent`) | **B** | LLM (vendor foundation model), customer-facing | Answers balance/transaction/history questions, routes to tools | Transactions product team | **High** — customer-facing, real account data | Offline CI gate: grounding accuracy ≥ 0.90, hallucination rate ≤ 0.05, tool utilization ≥ 0.90 (`transaction_agent_eval.jsonl`); live LLM-as-judge groundedness/instruction-following/safety scoring | Daily live-eval job (`scripts/live_eval.py`) → `conversation_scores`/`eval_summary`; Admin UI LIVE badge; Model Armor I/O screening at runtime | [eval/README](../eval/README.md), [ADR-0015](adr/0015-live-evaluation.md), [ADR-0008](adr/0008-model-armor-llm-screening.md) |
| M3 | Loan agents (Gemini 2.5 Flash via Vertex, `products/loans/agents`) | **B** | LLM (vendor foundation model), customer + officer-facing | Conversational loan application intake, status Q&A | Loans product team | **High** — feeds credit decisioning workflow | Same offline/live eval pattern as M2, scoped to loan flows | Same as M2 | [eval/README](../eval/README.md), [ADR-0015](adr/0015-live-evaluation.md) |
| M4 | Knowledge-base RAG retriever (BigQuery `VECTOR_SEARCH` + `ML.GENERATE_EMBEDDING` / Vertex `text-embedding`) | **B** | Retrieval model | Grounds unstructured Q&A (fees, policies, terms, branch info) for the Banking Assistant | Transactions product team / platform | **Medium** — informational, not a decision, but a hallucination source if retrieval is wrong | Retrieval correctness implicitly covered by the offline grounding-accuracy gate (answers must trace to tool/KB output); no standalone retrieval precision/recall benchmark yet | Governed BigQuery dataset (IAM, audit, lineage) — same controls as platform data; no automated staleness/drift monitor on corpus yet | [ADR-0009](adr/0009-bigquery-vector-rag.md) |
| M5 | Conversational Analytics / Data Agent (Gemini Data Analytics, `finchat-<env>-analyst`) | **B** | LLM (vendor foundation model) + semantic layer | Analyst-facing NL→SQL over curated serving views | Platform / analyst product team | **Medium** — read-only analytics, but SQL-generation + PII exposure risk | Verified query behavior against semantic-perimeter views (identifier columns structurally omitted); no labeled NL→SQL eval set yet (gap) | Same live-eval capture/score loop as M2/M3 captures analyst turns (SQL + result sample as grounding context) | [ADR-0012](adr/0012-conversational-analytics.md), [ADR-0018](adr/0018-analyst-semantic-perimeter.md) |
| M6 | LLM-judge (Gemini 2.5 Flash on Vertex, `scripts/live_eval.py`) | **B** | LLM (vendor foundation model), evaluator/control model | Scores groundedness, instruction-following, coherence, safety of M2/M3/M5 production turns | Platform / AgentOps | **Medium** — a control itself, not customer-facing, but its own drift undermines monitoring for the models it scores | No independent validation of judge accuracy against human-labeled ground truth yet (gap — judge is currently trusted as-is) | Cost-bounded via daily `--limit` sample; managed alternative documented (Vertex Gen AI Evaluation Service) | [ADR-0015](adr/0015-live-evaluation.md) |
| M7 | Model Armor screening templates | **B** | Rule/ML-based content & injection classifier (managed GCP service) | Screens prompt-injection/jailbreak, sensitive data, malicious URLs, harmful content at the LLM I/O boundary for M2 | Platform / security | **Medium** — a control, failure mode is fail-open (configurable to fail-closed) | Managed-service filters; not independently benchmarked by FinChat (vendor-validated) | Fail-open/fail-closed mode is an explicit config (`ARMOR_FAIL_CLOSED`); no FinChat-side monitoring of false-negative rate yet (gap) | [ADR-0008](adr/0008-model-armor-llm-screening.md) |

Agents are inventoried separately with per-agent identity, ownership and recertification in the
**agent registry** — see [20 — Agent Registry & Identity](20-agent-registry.md). The two are
complementary: this table registers *what reasons*, the registry registers *what acts*.

## Risk tiering rubric

- **High** — directly drives or materially informs a credit/financial decision, or is
  customer-facing with real account data in scope. Requires the full control cycle:
  documented development, validation independent of development, ongoing monitoring,
  outcomes analysis, and change control. For Regime A this tracks revised MRM guidance; for
  Regime B it is FinChat's own standard, deliberately held at the same bar.
- **Medium** — informational/analytical output, or a control model whose failure degrades
  a High-tier model's safety net rather than causing direct harm itself. Requires
  validation and monitoring, lighter documentation burden.
- **Low** — internal tooling with human-in-the-loop review and no direct customer/decision
  impact. (No current FinChat model qualifies as Low — everything customer- or
  analyst-facing touches real data or a real decision.)

**Note on tiering under Regime B.** Nothing in the revised guidance requires FinChat to tier
generative models at all. Holding M2/M3 at High is a deliberate choice: the exposure did not
decrease because the guidance was withdrawn, and a control framework that relaxed the moment
supervision stepped back would not survive the RFI.

## The FinChat control framework

What Regime B is governed by, since nothing external supplies it. Controls map to the
**FINOS AI Governance Framework** (v2, CC-BY-4.0, authored by member institutions including
Morgan Stanley, Citi, NatWest and RBC) rather than to a catalogue authored here.

| Control | Implementation | Evidence |
|---|---|---|
| Model version pinning | Pinned model snapshot per production call site, with the serving version logged per turn | [ADR-0022](adr/0022-model-version-pinning.md), `conversation_log.model_version` |
| Pre-release evaluation gate | Offline golden-set eval enforced in CI; release blocked on regression | [eval/README](../eval/README.md) |
| Post-release monitoring | Live LLM-judge scoring of production turns; scheduled canary against the golden set | [ADR-0015](adr/0015-live-evaluation.md), [ADR-0022](adr/0022-model-version-pinning.md) |
| I/O screening | Model Armor on prompt and response | [ADR-0008](adr/0008-model-armor-llm-screening.md) |
| Least-privilege data access | Analyst semantic perimeter; end-user credential propagation so BigQuery evaluates the caller's own IAM and policy tags | [ADR-0018](adr/0018-analyst-semantic-perimeter.md), [ADR-0019](adr/0019-end-user-credential-propagation.md) |
| Human accountability for consequential action | Human-in-the-loop approval gates; verified approver identity written to an append-only audit | [ADR-0016](adr/0016-identity-resolved-personas.md), [ADR-0021](adr/0021-durable-agent-harness.md) |
| Decision explainability | Per-factor attribution + ranked principal reason codes (Regime A; ECOA/Reg B) | [ADR-0013](adr/0013-loan-decision-explainability.md) |
| Agent identity & lifecycle | Distinct identity per agent, named owner, scoped tools, recertification date, deploy gate | [ADR-0023](adr/0023-agent-registry-and-identity.md), [20 — Agent Registry](20-agent-registry.md) |

## Known gaps (tracked, not yet closed)

1. **No standalone retrieval-quality benchmark for M4** — grounding accuracy is measured
   end-to-end (final answer vs. source), not retrieval precision/recall in isolation. A
   drop in retrieval quality could be masked by generation-side compensation.
2. **No judge-accuracy validation for M6** — the LLM-as-judge scoring M2/M3/M5 is not
   itself checked against a human-labeled sample. Standard practice is periodic
   human/judge agreement sampling; not yet implemented. Note the known bias literature:
   position bias, verbosity bias, and self-enhancement (models favour their own family's
   outputs) are measured effects, not hypothetical — M6 currently judges models from its
   own family, which is the configuration most exposed to self-enhancement bias.
3. **No FinChat-side false-negative tracking for M7** — Model Armor is a managed control;
   FinChat does not currently track how often it fails to catch an injection attempt that
   the offline eval's policy-refusal cases later reveal. No vendor publishes a prompt-injection
   detection rate; M7 is treated as defense-in-depth, never as a control boundary.
4. **No board-level AI governance policy document** — this inventory, the agent registry and
   the linked ADRs/eval docs provide the *evidence* a framework expects, but there is no
   senior-management-level policy doc, model owner sign-off record, or periodic revalidation
   cadence defined. Appropriate next step if this were a real production deployment rather
   than a reference architecture. This gap is larger than it was under SR 11-7, because there
   is now no external template to adopt.
5. **Third-party model risk has no purpose-built guidance.** SR 23-4 (third-party risk, 2023)
   does not mention AI. Vendor foundation models, inference APIs and agent frameworks are
   governed here by contract review and version pinning alone.

## Maintenance

Add a row here whenever a new model (LLM call site, ML model, retrieval index, or
evaluator) is introduced anywhere in the platform, and update the ADR/eval links if
validation approach changes. Register the *agent* in [20 — Agent Registry](20-agent-registry.md)
as well if it takes tool-using action. This table is the answer to "what models does FinChat run
and how do we know they're safe" — keep it current rather than re-deriving it from code.
