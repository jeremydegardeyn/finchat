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

## Deploy

**Cloud Run (default — true scale-to-zero, ~$0 idle).** Built & deployed by CI/CD
(`build-deploy.yml` → service `finchat-<env>-agent`). It serves `server.py`
(FastAPI + ADK Runner) with `POST /chat`; Gemini runs via Vertex (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`,
runtime SA has `roles/aiplatform.user`). Manual:
```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
gcloud builds submit --tag $REPO/agent .
gcloud run deploy finchat-dev-agent --image $REPO/agent --region us-central1 \
  --service-account finchat-dev-agent@strongsville-city-schools.iam.gserviceaccount.com \
  --set-env-vars GOOGLE_CLOUD_PROJECT=strongsville-city-schools,GOOGLE_CLOUD_LOCATION=us-central1,GOOGLE_GENAI_USE_VERTEXAI=TRUE,TXN_API_URL=<txn-api-url>
curl -XPOST <agent-url>/chat -H 'content-type: application/json' -d '{"message":"balance for acct-001"}'
```
Tools call the (private) txn-api with an **OIDC id-token** (the agent SA has `run.invoker`).

**Agent Engine (optional)** — managed sessions/eval/tracing, but **bills a per-engine
baseline (~$75–110/mo, no scale-to-zero)**; we chose Cloud Run for near-zero cost (ADR-0004):
```bash
python deploy.py --project strongsville-city-schools --location us-central1 \
  --staging-bucket gs://finchat-dev-dataflow
```

## Evaluation

Starter eval set: [`../../../eval/datasets/transaction_agent_eval.jsonl`](../../../eval/datasets/transaction_agent_eval.jsonl).
Full grounding/hallucination/tool-use metrics + pipeline land in Increment 7
([eval/](../../../eval/)).
