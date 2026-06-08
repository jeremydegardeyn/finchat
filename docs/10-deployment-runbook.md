# 10 — Deployment Runbook

> Step-by-step to deploy FinChat to `strongsville-city-schools`. CI/CD + promotion: [cicd/](../cicd/README.md).

## Prerequisites

- `gcloud`, `terraform >= 1.8`, `docker`, `python 3.12`.
- Authenticated: `gcloud auth login` + `gcloud auth application-default login`.
- Owner/Editor on the project (first apply creates IAM, datasets, etc.).

> **Quota project note:** the providers set `billing_project + user_project_override`, so API calls
> self-attribute to the project — no `gcloud auth application-default set-quota-project` needed.

## 1. Bootstrap remote state

```bash
./scripts/bootstrap_state.sh strongsville-city-schools us-central1
```

## 2. Provision infrastructure (per env)

```bash
cd infra/envs/dev
cp terraform.tfvars.example terraform.tfvars   # (already populated for dev/test/prod)
terraform init
terraform plan
terraform apply
```

Creates: APIs, service accounts + IAM, Artifact Registry, buckets, BigQuery medallion (CLS/RLS),
Pub/Sub + DLQ + BQ subscription, DLP templates, monitoring + audit sink. Enterprise toggles
(`enable_streaming_job`, `run_min_instances`, `enable_api_gateway`, `enable_workflows`) default OFF.

### Known first-apply gotchas (all resolved in code)
- **DLP / budget 403 "quota project"** → fixed by provider `billing_project` (above).
- **Gold view 404** → fixed via `depends_on` on Silver tables.
- **Custom role 400** → removed unsupported `workflowexecutions.*` permissions.
- **DLP 400 surrogate** → `surrogate_info_type` added.
- **Budget 403 (billing perms)** → set `enable_budget=false` if you lack `billing.budgets.create`.

## 3. Load schemas (if not using TF-managed tables)

```bash
sed -e 's/${PROJECT}/strongsville-city-schools/g' -e 's/${ENV}/dev/g' \
  products/transactions/schemas/ddl.sql | bq query --use_legacy_sql=false
sed -e 's/${PROJECT}/strongsville-city-schools/g' -e 's/${ENV}/dev/g' \
  products/loans/schemas/ddl.sql | bq query --use_legacy_sql=false
```

## 4. Build & deploy services

Via CI/CD (preferred): push to `main` → `build-deploy.yml`. Manually:

```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
for s in "products/transactions/api:txn-api" "products/loans/api:loan-api" "ui:ui"; do
  ctx=${s%%:*}; name=${s##*:}
  gcloud builds submit --tag $REPO/$name $ctx
done
gcloud run deploy finchat-dev-txn-api --image $REPO/txn-api --region us-central1 \
  --service-account finchat-dev-txn-api@strongsville-city-schools.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT=strongsville-city-schools,GOLD_DATASET=finchat_gold_dev,SILVER_DATASET=finchat_silver_dev \
  --no-allow-unauthenticated
# repeat for loan-api (private) and ui (public, with LOAN_API_URL/TXN_API_URL)
```

## 5. Build the Dataflow Flex Template

```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
gcloud builds submit --tag $REPO/txn-pipeline products/transactions/pipeline
gcloud dataflow flex-template build gs://finchat-dev-dataflow/templates/txn-pipeline.json \
  --image $REPO/txn-pipeline --sdk-language PYTHON \
  --metadata-file products/transactions/pipeline/metadata.json
```

## 6. Generate data + run pipeline (on-demand)

```bash
python products/transactions/generator/generate.py --count 5000 \
  --project strongsville-city-schools --topic finchat-dev-transactions-ingest

# Optional DLP de-identification: grab the template names from terraform output.
DEID=$(terraform -chdir=infra/envs/dev output -raw dlp_deidentify_template)
INSPECT=$(terraform -chdir=infra/envs/dev output -raw dlp_inspect_template)

gcloud dataflow flex-template run "txn-stream-$(date +%s)" \
  --template-file-gcs-location gs://finchat-dev-dataflow/templates/txn-pipeline.json \
  --region us-central1 --project strongsville-city-schools \
  --temp-location gs://finchat-dev-dataflow/temp --staging-location gs://finchat-dev-dataflow/staging \
  --service-account-email finchat-dev-pipeline@strongsville-city-schools.iam.gserviceaccount.com \
  --parameters input_subscription=projects/strongsville-city-schools/subscriptions/finchat-dev-transactions-dataflow,\
output_table=strongsville-city-schools:finchat_silver_dev.transaction,\
dlq_topic=projects/strongsville-city-schools/topics/finchat-dev-transactions-dlq,\
deid_template=$DEID,inspect_template=$INSPECT,dlp_sample_rate=0.2

# Omit deid_template/inspect_template to skip DLP. The pipeline SA already has roles/dlp.user.
```

(Or rely solely on the Pub/Sub→BigQuery subscription for raw Bronze; drain the Dataflow job when done.)

### 6b. Seed customer/account dimensions

The generator emits only transaction facts. Backfill the `customer`/`account` dimension
tables (1:1 per observed account) so the Gold balance/summary views — which join `FROM account` —
return data:

```bash
./scripts/seed_dimensions.sh dev      # idempotent; re-run after more data lands
```
After this, `GET /v1/accounts/<id>/balance` and `/summary` return 200 for any account id present
in `silver.transaction`.

## 7. Deploy agents (Agent Engine)

```bash
cd products/transactions/agent && pip install -r requirements.txt && \
  python deploy.py --project strongsville-city-schools --location us-central1 \
  --staging-bucket gs://finchat-dev-dataflow
cd ../../loans/agents && python deploy.py --project strongsville-city-schools \
  --location us-central1 --staging-bucket gs://finchat-dev-dataflow
```

## 8. Smoke test

```bash
UI=$(gcloud run services describe finchat-dev-ui --region us-central1 --format='value(status.url)')
open "$UI"   # switch personas; submit a loan; chat
```

## 9. Custom domain (finchat.datadinosaur.com)

The UI is a **Cloud Run service** — map a custom domain onto it (no VM, no separate host).

```bash
# a) Verify you own the domain for this project (one-time)
gcloud domains verify datadinosaur.com         # opens Search Console; follow the TXT-record step

# b) Enable + apply the mapping (prod)
#    set in infra/envs/prod/terraform.tfvars:  custom_domain = "finchat.datadinosaur.com"
cd infra/envs/prod && terraform apply

# c) Read the DNS records Terraform returns, then add them at datadinosaur.com
terraform output ui_custom_domain_dns_records
```

Add the returned record at your DNS provider for `datadinosaur.com` — typically a **CNAME** on the
`finchat` subdomain pointing to `ghs.googlehosted.com` (Google returns the exact rrdata). Google then
auto-provisions a **managed TLS cert** (a few minutes). Done — `https://finchat.datadinosaur.com`
serves the UI.

> **Cost:** Cloud Run domain mapping is **free** (near-zero posture).
> **Enterprise target:** a **Global External HTTPS Load Balancer** + serverless NEG + managed cert +
> **Cloud Armor** (WAF/DDoS) — better for apex domains, multi-region, and edge security. Same DNS swap;
> ~$18/mo for the forwarding rule. Documented as the H1 upgrade in [11-roadmap](11-future-state-roadmap.md).

## Rollback

- **Services:** redeploy a previous image tag (`gcloud run deploy --image .../<svc>:<old-sha>`); Cloud Run keeps revisions.
- **Infra:** `git revert` the change + `terraform apply`; state is versioned in GCS.
- **Data:** Bronze is immutable → reprocess Silver/Gold from Bronze (no re-ingestion).

## Teardown

```bash
cd infra/envs/dev && terraform destroy
```
