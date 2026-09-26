# ADR-0015 — Live evaluation (score real production conversations)

- **Status:** Accepted
- **Date:** 2026-06-10
- **Deciders:** Principal Data Architect
- **Context tags:** AgentOps, evaluation, observability, responsible AI

## Context

[ADR-0007-era] `eval/pipelines/evaluate.py` is an offline CI gate: it scores a fixed
dataset of prompts and blocks merges on grounding / tool-use / approval thresholds.
That proves the contract holds for known cases, but it says nothing about what the
*deployed* assistants actually do on *real* traffic. The Admin → Evaluations card was
showing those offline numbers (or blanks). We want live evaluation: score actual
production conversations and surface rolling quality metrics.

## Decision

Add a capture → score → surface loop:

- **Capture** — the UI BFF logs every conversation turn (customer banking assistant and
  analyst analytics/KB) best-effort, fire-and-forget on a daemon thread, into
  `finchat_eval_<env>.conversation_log` (question, answer, grounding context, persona,
  channel). For analytics turns the generated SQL + a sample of result rows are stored
  as the grounding context.
- **Score** — `scripts/live_eval.py` samples recent un-scored turns and has **Gemini on
  Vertex** act as an LLM-as-judge, rating each turn for groundedness,
  instruction-following, coherence, and safety. Scores land in `conversation_scores`.
  Runs daily as the CI/CD SA via `.github/workflows/live-eval.yml` (schedule +
  workflow_dispatch); the managed alternative is the **Vertex AI Gen AI Evaluation
  Service** (`eval/pipelines/vertex_eval.py`).
- **Surface** — `eval_summary` is a rolling 7-day view of normalized metrics + sample
  size; `/api/eval` prefers it and the Admin card shows a **LIVE** badge, falling back
  to the baked offline report when there's no live data yet.

## Rationale

- **Two layers, by design** — offline CI proves the contract on known cases and gates
  merges; live eval observes real behavior and trends. Neither replaces the other.
- **Groundedness where we have context** — analytics turns carry their SQL + data, so
  groundedness is judged against real evidence; for agent/KB turns (no exposed tool
  trace) the judge returns null groundedness and scores the reference-free metrics.
- **Cheap automation, enterprise mapping** — a scheduled GitHub Action + the CI/CD SA
  (no new runtime infra) is the near-zero-cost tier; the enterprise mapping is a Cloud
  Run Job on Cloud Scheduler, or the managed Vertex Gen AI Evaluation Service.
- **Privacy** — logs are governed in a dedicated `finchat_eval_<env>` dataset; the
  analyst surface already excludes direct PII (customer_id + segment only), and
  question/answer text is truncated.

## Consequences

- New `finchat_eval_<env>` dataset (bigquery module) + `scripts/eval_schema.sql`
  (`conversation_log`, `conversation_scores`, `eval_summary`); BFF (txn_api) + CI/CD SA
  get dataEditor on it; CI/CD SA also gains `bigquery.jobUser` + `aiplatform.user`.
- UI deploy gains `EVAL_DATASET`. Capture/scoring degrade gracefully if the dataset
  isn't applied yet (best-effort writes no-op; `/api/eval` falls back to offline).
- Judge cost is bounded by the daily `--limit` sample. Tune cadence/limit per traffic.

## Alternatives considered

- **Offline eval only:** rejected — never observes real traffic or drift.
- **Vertex Gen AI Evaluation Service for the live loop:** kept as the managed mapping;
  the Gemini-judge script is the portable, controllable, low-dependency default.
- **Cloud Run Job + Scheduler for the scorer:** the enterprise mapping; the scheduled
  GitHub Action reuses existing WIF + the CI/CD SA at zero added infra.

## Update — 2026-09-25: the managed mapping now runs

This ADR named `eval/pipelines/vertex_eval.py` as the managed alternative, and
`scripts/live_eval.py` and `eval/README.md` both cited it. It could not run. Its dataframe
set `response` to the literal string `"<deployed-agent-response>"` with the Agent Engine
call commented out, so a run would have scored seven copies of a placeholder and reported
metrics on them. It also imported `vertexai.preview.evaluation`, superseded by the
`vertexai.Client(...).evals` surface with different metric names.

The decision above is unchanged — the portable Gemini judge stays the default for the live
loop, for the reason in Rationale and one that has hardened since: ADR-0022 requires the
serving version to be recorded per scored row, and a managed autorater's version is the
provider's, not ours. What changed is that the managed tier is now a loop that executes:

- **It scores the golden set against the live agent**, not captured traffic — so it is the
  canary's dataset with a judge FinChat did not write, and deliberately not a replacement
  for either neighbour.
- **The agent reports its trajectory.** `POST /chat` takes `include_trajectory` and returns
  the tool calls, the tool output and the ADK event trace. Without the tool output there is
  nothing to ground a hallucination judgement on except the answer's own prose; without the
  calls, tool selection cannot be scored at all. Off by default: the customer path proxies
  that body to the browser verbatim.
- **Adaptive rubrics** (per-prompt pass/fail tests generated by the service) plus a
  **static rubric** for the refusal policy, one criterion per rule, derived at runtime from
  the frontmatter of `knowledge/playbooks/refusal-escalation.md` rather than copied.
- **Grounding is its own phase**, because `hallucination_v1` reads its evidence from the
  prompt and nowhere else. Measured 2026-09-26: a fabricated balance scored 1.0 when the
  tool output was supplied as a `context` column, as `instruction` or as `reference`, and
  0.0 when it was in the prompt. In the first three the metric had no evidence, so it
  scored the answer against itself — the per-case rationale handed back the response as its
  own supporting excerpt. The first live run's `hallucination_v1: 1.0` measured nothing.
- **The refusal rubric has to name the surface it audits.** Its rules are compiled from a
  list written for the analyst perimeter, so on first run it failed the customer agent for
  summarising the caller's own account and for asking which account they meant — both being
  that agent's job. Stating the surface cleared both. The durable fix is a per-surface
  applicability field in the SSOT, which changes what the agents are instructed with and so
  belongs to the policy's owner rather than to this pipeline.
- **Tool metrics are the computation-based ones** — `tool_call_valid`, `tool_name_match`,
  and the two parameter matchers reported rather than gated. The `trajectory_exact_match`
  family the old file's comment promised does not exist in the SDK yet; ordered-trajectory
  scoring stays in `evaluate.py`, deterministic and free.
- **Still not in CI.** Judge tokens per rubric per case. CI runs the unit tests and
  `--dry-run`, both free; the paid run is `workflow_dispatch` only.
- **It reports; it does not gate the tool metrics.** First live run against dev revision
  00084 scored `tool_call_valid` and `tool_name_match` at 1.00 and the two parameter
  matchers at 0.93. The second run, same revision, same prompts, scored 0.86/0.86/0.79:
  `not-found` called the balance tool on one run and no tool on the other. Seven cases means
  one flip moves a mean by 0.143, so any threshold worth setting is finer than the
  measurement, and a gate that fires on sampling noise gets muted — the failure mode
  ADR-0022's canary explicitly guards against. Baseline-relative drift detection on this
  dataset stays with `canary_eval.py`, which has the tolerance bands for it. Only
  `refusal_policy` is gated, because a policy breach is categorical rather than statistical.
  The parameter matchers are additionally unfit to gate: the agent passes `limit: 3` for
  "my last 3 transactions", which is correct and which the label does not carry.

### A second gap this closed

ADR-0022's evidence half was not working on the agent surface. `scripts/canary_eval.py`
read `model_version` off `/chat`; `/chat` never returned such a field, and ADK's
`LlmResponse.create()` discards Vertex's `modelVersion` outright, so there was nothing to
return. Every canary run recorded `served: None` and `version_changed()` could not fire —
the control's headline claim, "quality fell the day the serving version changed", was
unreachable. `gateway_llm` now carries the served version through ADK's `custom_metadata`
(which `_finalize_model_response_event` merges into the Event), `/chat` reports it as
`model_served`, and the canary reads that name. Where the surface genuinely does not report
a version — the ungoverned direct-to-Vertex fallback — it stays `None` rather than being
backfilled from the requested id, and `test_trajectory.py` asserts that.
