# Loan API

Loan submission, status, and **authenticated, append-only approver decisions**. FastAPI on Cloud Run.

## Endpoints

| Method · Path | Persona | Purpose |
|---|---|---|
| `POST /v1/loans` | Customer | Submit (name, amount, term[, account_id]) → validate, profile, risk-score, route |
| `GET /v1/loans/{id}` | Customer | Status + latest risk + decision history |
| `GET /v1/loans` | Employee | List requests (optional `?status=`) |
| `GET /v1/loans/{id}/audit` | Employee | Full audit trail |
| `POST /v1/loans/{id}/decision` | Employee | Approve / Reject / Request-Modification / Counteroffer (requires `X-Approver` header) |

## Decision model

- Decisions are **INSERT-only** and **versioned** (`loan_id` + `version`) → complete, immutable history.
- `X-Approver` carries the authenticated identity (simulated by header here; **IAM/IAP** in prod).
- `/decision` is also the **Cloud Workflows callback target** for the long-running HITL path.

## Run locally (demo, no GCP)

```bash
pip install -r requirements.txt
DEMO_MODE=1 uvicorn main:app --port 8081
# submit
curl -XPOST localhost:8081/v1/loans -H 'content-type: application/json' \
  -d '{"customer_name":"Jeremy D","amount":15000,"term_months":36,"account_id":"acct-001"}'
# decide (employee)
curl -XPOST localhost:8081/v1/loans/<id>/decision -H 'X-Approver: jeremy@datadinosaur.com' \
  -H 'content-type: application/json' -d '{"decision":"APPROVE","rationale":"ok"}'

pytest test_risk.py   # 6 risk-scoring tests
```

Set `TXN_API_URL` to pull overdraft history from the Transactions product (cross-product lineage).

## Synchronous vs orchestrated

`POST /v1/loans` runs the steps inline for the demo/UI. The **enterprise** long-running path is
[`../workflow/loan_approval.yaml`](../workflow/loan_approval.yaml) driving the 5 ADK agents
([`../agents/`](../agents/)) with a durable human-approval wait.
