# Data Product 1 — Banking Transactions

Real-time retail-banking transaction ledger: event-driven ingestion → BigQuery Medallion → DaaS
APIs → conversational agent. Architecture: [data flow](../../docs/03-data-flow.md),
[API](../../docs/05-api-architecture.md), [agent](../../docs/04-agent-architecture.md),
[data model](../../docs/data-model.md).

## Components

| Dir | What | Verified |
|-----|------|----------|
| [`generator/`](generator/) | Synthetic transaction generator (≤10k/run, ≤4/customer, realistic mix, seeded overdrafts) | ✅ invariant test |
| [`pipeline/`](pipeline/) | Apache Beam / Dataflow streaming (validate → DLP → Silver, DLQ routing) | ✅ DirectRunner run + 10 unit tests |
| [`schemas/`](schemas/) | BigQuery DDL + JSON message schema | — |
| [`api/`](api/) | DaaS API (FastAPI, OpenAPI 3) — balance, history, activity, summary | ✅ data layer test |
| [`agent/`](agent/) | ADK + Gemini conversational data agent (tool-calling, grounded) | ✅ offline grounding |

## End-to-end local smoke (no GCP)

```bash
# 1. Generate sample transactions
python generator/generate.py --count 100 --dry-run > /tmp/txns.jsonl

# 2. Run them through the real Beam graph (validation + DLQ)
cd pipeline && python pipeline.py --input_file /tmp/txns.jsonl --output_file valid --dlq_file dlq

# 3. Serve the API on demo data
cd ../api && DEMO_MODE=1 uvicorn main:app --port 8080 &

# 4. Ask the agent (uses the API for grounding)
cd ../agent && TXN_API_URL=http://localhost:8080 python agent.py
```

## Deploy order

`terraform apply` (Increment 2) → build+push images (generator, pipeline, api) → build Flex Template →
deploy Cloud Run services → deploy agent to Agent Engine → run generator → launch Dataflow on-demand.
Full steps: [deployment runbook](../../docs/10-deployment-runbook.md) (Increment 6).
