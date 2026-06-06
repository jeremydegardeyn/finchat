# Data Product 2 — Loan Approval

Long-running agentic loan-approval workflow with orchestration, AI decision support, human-in-the-loop
approval, and full auditability. Architecture: [agent](../../docs/04-agent-architecture.md),
[data model](../../docs/data-model.md); decisions in [ADR-0005](../../docs/adr/0005-workflows-vs-composer.md).

## Components

| Dir | What | Verified |
|-----|------|----------|
| [`schemas/`](schemas/) | DDL: loan_request, credit_profile, risk_assessment, **append-only** approval_decision + loan_audit_log, `loan_status` view | — |
| [`api/`](api/) | Loan API (submit, status, list, audit, **authenticated decision**) + pure risk/credit logic | ✅ 6 risk tests + flow test |
| [`agents/`](agents/) | 5 ADK agents (Planner/Credit/Review/Approval/Notification) | ✅ offline orchestration |
| [`workflow/`](workflow/) | Cloud Workflows orchestration with **HITL callback** (11 steps) | ✅ YAML parses |

## The 13 required workflow steps → where they live

| Step | Implemented by |
|------|----------------|
| 1 validate · 2 request missing | workflow `validate_submission` + API pydantic |
| 3 create record | API `create_loan` |
| 4 synth credit profile · 5 store | API `synthesize_credit_profile` + `save_profile` / Credit Agent |
| 6 retrieve txn history · 7 overdraft eval | `_overdraft_events` via Transactions DaaS / Transaction Review Agent |
| 8 risk score · 9 recommendation | `score_risk` / Approval Agent |
| 10 route to human | workflow `create_callback_endpoint` + `await_callback` |
| 11 capture decision · 12 update status | API `record_decision` (append-only, versioned) |
| 13 notify customer | API `/notify` / Notification Agent |

## Human-in-the-loop

- Approver actions: **Approve · Reject · Request Modification · Counteroffer** (amount).
- Every decision is **auditable, timestamped, versioned (append-only), and stored** — full history
  reconstructable via the `loan_status` view + `approval_decision` rows.
- Authenticated approver identity via `X-Approver` (simulated; **IAM/IAP** in prod).

## Long-running state

Cloud Workflows is the durable state machine; its execution survives the multi-hour/day approval wait
through a callback endpoint. BigQuery is the system-of-record. Agent Engine managed sessions hold
agent context. See [docs/04](../../docs/04-agent-architecture.md#state-across-long-running-executions).

## Local smoke (no GCP)

```bash
cd api && DEMO_MODE=1 uvicorn main:app --port 8081 &
curl -XPOST localhost:8081/v1/loans -H 'content-type: application/json' \
  -d '{"customer_name":"Jeremy D","amount":15000,"term_months":36}'
cd ../agents && python agents.py     # multi-agent underwriting (offline)
```
