# FinChat Banking Assistant (ADK + Gemini)

Conversational data agent that answers account questions grounded in the Transactions data product
via tool calling. Enterprise pattern per [ADR-0004](../../../docs/adr/0004-agent-engine-vs-mcp.md):
authored in **Google ADK**, deployable to **Vertex AI Agent Engine** (managed sessions, tracing,
eval) or **Cloud Run** (portable fallback).

## Tools (grounding)

| Tool | Backs onto |
|------|-----------|
| `get_account_balance` | DaaS `/balance` (Gold `account_balance`) |
| `get_transaction_history` | DaaS `/transactions` (Silver `transaction`) |
| `get_account_summary` | DaaS `/summary` (Gold `account_summary`) |

Every answer is grounded in tool results; the system instruction forbids fabricating financial data.

## Run locally

```bash
pip install -r requirements.txt
export TXN_API_URL=http://localhost:8080      # the DaaS API (or omit for demo fallback)
adk run .        # interactive CLI
adk web          # local web UI at http://localhost:8000
```

No ADK installed? `python agent.py` runs a naive intent-router over the same grounding tools (dev
only) so the data path is exercisable offline.

## Deploy to Agent Engine

```bash
python deploy.py --project strongsville-city-schools --location us-central1 \
  --staging-bucket gs://finchat-dev-dataflow
```

## Evaluation

Starter eval set: [`../../../eval/datasets/transaction_agent_eval.jsonl`](../../../eval/datasets/transaction_agent_eval.jsonl).
Full grounding/hallucination/tool-use metrics + pipeline land in Increment 7
([eval/](../../../eval/)).
