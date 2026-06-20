# Steward Harness — durable agent loop (Increment 19)

The reusable durable-execution engine behind the Reconciliation / Data-Quality
Steward. Planner → Generator → Evaluator loop that **sleeps at zero cost**,
**survives crashes**, and **wakes on the approver / events** — no polling.

See [docs/18-durable-agent-harness.md](../../../docs/18-durable-agent-harness.md)
and [ADR-0021](../../../docs/adr/0021-durable-agent-harness.md).

## Files

| File | Role |
|---|---|
| `harness.py` | The durable loop (DBOS workflow + steps, sleep, signals, status) |
| `planner.py` / `generator.py` / `evaluator.py` | The three reasoning roles |
| `tools.py` | Real BigQuery data-quality / reconciliation checks (row count + freshness; offline-safe stub when no GCP_PROJECT) |
| `llm.py` | Gemini **via Vertex AI** (no API key; offline fallback) |
| `main.py` | FastAPI front door (start / status / review) |
| `test_offline.py` | Offline unit tests for the reasoning logic |

## Run offline tests (no Postgres, no key)

```bash
cd products/steward/harness
python -m pytest -q
```

## Run the durable harness locally

Needs a Postgres (the durable "autosave"). Quickest:

```bash
docker run -d --name finchat-steward-pg -p 5432:5432 \
  -e POSTGRES_PASSWORD=dbos -e POSTGRES_DB=finchat_steward postgres:16
pip install -r requirements.txt
export DBOS_DATABASE_URL=postgresql://postgres:dbos@localhost:5432/finchat_steward
uvicorn main:app --port 8083
```

Drive it:

```bash
WID=$(curl -s -X POST localhost:8083/runs -H 'content-type: application/json' \
  -d '{"goal":"Reconcile yesterday ledger and flag anomalies"}' | jq -r .workflow_id)
curl -s localhost:8083/runs/$WID            # phase -> awaiting_human at the anomaly step
curl -s -X POST localhost:8083/runs/$WID/review -H 'content-type: application/json' \
  -d '{"approved":true,"approver":"jeremy@datadinosaur.com","note":"reviewed"}'
curl -s localhost:8083/runs/$WID            # -> done
```

**Durability demo:** kill the server mid-run (or while parked at `awaiting_human`)
and restart it — DBOS logs `Recovering N workflows` and resumes from the last
checkpoint; completed steps are not re-run.

## Deploy

Cloud Run (scale-to-zero) + Cloud SQL Postgres via `infra/modules/agent_harness`
(toggle `enable_agent_harness`, default **off**). Enterprise 1:1 = Temporal (ADR-0021).
