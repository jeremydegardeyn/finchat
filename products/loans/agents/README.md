# Loan Multi-Agent System (ADK + Gemini)

Five specialist agents underwrite a loan and route it to a human approver. Diagram + roles:
[docs/04-agent-architecture.md](../../../docs/04-agent-architecture.md).

| Agent | Tool | Job |
|-------|------|-----|
| Planner | — (coordinator) | Validate request, decompose, coordinate, summarize for the approver |
| Credit | `generate_credit_profile` | Synthetic credit profile (deterministic) |
| Transaction Review | `get_overdraft_history` | Overdraft signal from the Transactions DaaS (cross-product) |
| Approval | `compute_risk` | Risk score + recommendation (auditable thresholds) |
| Notification | `send_notification` | Customer comms |

Specialists run as a `SequentialAgent` (credit → review → approval); the **Planner** is `root_agent`.
**Human approval** happens between approval and notification, driven by **Cloud Workflows**
(`../workflow/loan_approval.yaml`) via a durable callback — so long-running state lives in the
Workflows execution + BigQuery, not the agent process.

## Run locally

```bash
pip install -r requirements.txt
export TXN_API_URL=http://localhost:8080      # for real overdraft lookups (optional)
adk run .          # interactive
python agents.py   # offline deterministic orchestration (no ADK needed)
```

## Deploy

```bash
python deploy.py --project strongsville-city-schools --location us-central1 \
  --staging-bucket gs://finchat-dev-dataflow
```
