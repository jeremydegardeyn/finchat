# FinChat Web UI

Lightweight single-page app with **simulated login personas** (no production IdP — role simulation for
demo). A FastAPI **backend-for-frontend (BFF)** serves the SPA and proxies to the backend services so
the browser never holds backend URLs and CORS is avoided.

## Personas & views

| Persona | View | Features |
|---------|------|----------|
| Customer | Customer | Submit loan request · view loan status · chat with the Banking Assistant |
| Loan Officer | Employee | Review request queue · Approve/Reject/Modify/Counteroffer · view audit trail |
| Platform Admin | Admin | Monitor pipelines · monitor agents · view evaluation metrics · backend health |

Switch persona via the header dropdown; the BFF injects the persona as `X-Persona` and, for employee
write actions, as `X-Approver` on the upstream call.

## Run locally

```bash
pip install -r requirements.txt
uvicorn server:app --port 8082          # standalone: SPA uses embedded DEMO data
# wire to real backends:
LOAN_API_URL=http://localhost:8081 TXN_API_URL=http://localhost:8080 \
  AGENT_URL= uvicorn server:app --port 8082
open http://localhost:8082
```

When no backend is configured the SPA shows a **DEMO DATA** badge and serves embedded sample data, so
the full UX is demoable with zero infrastructure.

## Deploy

Managed by the Terraform `cloud_run` module (`finchat-<env>-ui`, public). CI builds the image; set
`LOAN_API_URL` / `TXN_API_URL` / `AGENT_URL` env vars to the deployed service URLs. In production the
BFF would add OIDC id-tokens for the private Cloud Run backends (noted in `server.py`).
