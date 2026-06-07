# AgentOps — Evaluation Framework

Evaluation datasets, pipelines, and reporting for FinChat's agents — the **AgentOps** discipline that
makes agents production-fit for a regulated bank (measured, gated, monitored).

## Two-layer evaluation

| Layer | Tool | Cost | When |
|-------|------|------|------|
| **Offline contract + logic gate** | [`pipelines/evaluate.py`](pipelines/evaluate.py) | $0 (no LLM) | every CI run |
| **Live LLM-as-judge** | [`pipelines/vertex_eval.py`](pipelines/vertex_eval.py) (Vertex Gen AI Eval) | Gemini tokens | post-deploy, scheduled |

The offline harness validates the **grounding contract** (answers only assert tool-sourced facts),
**tool selection**, policy refusals, and the **loan decision logic** vs. labeled ground truth — fast
and free, so it gates merges. The live harness scores the deployed agents with judge metrics
(groundedness, instruction-following, safety, tool-call/trajectory quality).

## Metrics

| Metric | Definition | Source | Gate |
|--------|------------|--------|------|
| Grounding accuracy | answers whose asserted facts all trace to tool output | offline + Vertex groundedness | ≥ 0.90 |
| Hallucination rate | answers asserting unsupported facts | offline | ≤ 0.05 |
| Tool utilization | correct tool selected for the intent | offline + trajectory | ≥ 0.90 |
| Response quality | relevance + policy compliance (refusals, no advice, no cross-customer) | offline + Vertex | — |
| Approval rec. accuracy | loan recommendations vs. labeled outcomes | offline | ≥ 0.80 |

## Datasets

- [`datasets/transaction_agent_eval.jsonl`](datasets/transaction_agent_eval.jsonl) — balance/history/summary, missing-id, not-found, advice-refusal, cross-customer.
- [`datasets/loan_eval.jsonl`](datasets/loan_eval.jsonl) — labeled applicant profiles → expected recommendation.

## Run

```bash
python pipelines/evaluate.py                 # offline; writes reports/latest.json; non-zero exit on threshold breach
python pipelines/vertex_eval.py --project strongsville-city-schools --location us-central1  # live agents
```

Latest offline run (committed sample): [`reports/latest.json`](reports/latest.json).

## Reporting & dashboard strategy

1. **CI gate** — `evaluate.py` runs in `ci.yml`; a regression below threshold fails the build (quality is a release gate, not an afterthought).
2. **Report artifact** — `reports/latest.json` is the machine-readable scorecard; CI uploads it as a build artifact and (live) writes `vertex_latest.json`.
3. **Trend store** — push each run's `summary` to a BigQuery `eval_results` table (run_id, metric, value, ts) for longitudinal tracking.
4. **Dashboard** — Looker Studio over that table (or Cloud Monitoring custom metrics); the **Admin UI** Evaluations panel surfaces the latest summary to operators.
5. **Alerting** — Monitoring alert when grounding/approval accuracy drops or hallucination rises between runs (model/agent drift).

## Why this matters (regulated banking)

Auditable, versioned, threshold-gated evaluation is what lets a bank put an agent in front of customer
data and credit decisions: it provides evidence of accuracy, demonstrates non-fabrication controls,
and ties to model-risk-management expectations (e.g., SR 11-7).
