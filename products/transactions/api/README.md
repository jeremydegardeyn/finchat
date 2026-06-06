# Transactions DaaS API

Data-as-a-Service over the BigQuery Gold serving layer. FastAPI on Cloud Run, fronted by API
Gateway (ADR-0006). Contract-first — OpenAPI 3 at `/openapi.json`; the gateway uses
[`openapi.gateway.yaml`](openapi.gateway.yaml) (Swagger 2.0), which imports 1:1 into Apigee.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/accounts/{id}/balance` | Current balance |
| GET | `/v1/accounts/{id}/transactions?limit=` | Transaction history |
| GET | `/v1/accounts/{id}/activity?days=` | Recent activity |
| GET | `/v1/accounts/{id}/summary` | Account summary (Gold view) |
| GET | `/healthz` | Liveness + active data source |

## Run locally (demo data, no GCP)

```bash
pip install -r requirements.txt
DEMO_MODE=1 uvicorn main:app --reload --port 8080
curl localhost:8080/v1/accounts/acct-001/balance
curl localhost:8080/openapi.json | jq '.paths | keys'
```

`DEMO_MODE=1` serves a deterministic in-memory dataset (accounts `acct-001..003`). Without it the
API queries BigQuery using `GCP_PROJECT`, `GOLD_DATASET`, `SILVER_DATASET`; if credentials/project
are absent it degrades gracefully to demo data.

## Deploy

```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
gcloud builds submit --tag $REPO/txn-api .
gcloud run deploy finchat-dev-txn-api --image $REPO/txn-api --region us-central1 \
  --service-account finchat-dev-txn-api@strongsville-city-schools.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT=strongsville-city-schools,GOLD_DATASET=finchat_gold_dev,SILVER_DATASET=finchat_silver_dev \
  --no-allow-unauthenticated
```

The Terraform `cloud_run` module also manages this service; `gcloud run deploy` is the CI/CD path
(image updates are ignored by Terraform — ADR-0007 notes).

## Security
- Private by default (`--no-allow-unauthenticated`); invoked only by API Gateway SA / authorized callers.
- Runs as a least-privilege SA with `dataViewer` on Gold only.
- `maximum_bytes_billed` cap on every query (cost guardrail).
