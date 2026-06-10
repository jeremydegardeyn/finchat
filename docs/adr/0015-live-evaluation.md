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
