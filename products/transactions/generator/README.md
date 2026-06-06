# Synthetic Transaction Generator

Generates realistic retail-banking transactions and publishes them to Pub/Sub (or a file for
offline runs). Honors the platform constraints: ≤10,000 txns/run, ≤4 txns/customer/run, configurable
volume, deposits/withdrawals/transfers/fees, and seeded overdraft sequences for the loan product.

## Run offline (no GCP)

```bash
python generate.py --count 200 --out sample.jsonl      # write JSON lines
python generate.py --count 20  --dry-run               # print to stdout
```

## Publish to Pub/Sub

```bash
pip install -r requirements.txt
python generate.py --count 5000 \
  --project strongsville-city-schools \
  --topic finchat-dev-transactions-ingest
```

## Deploy as a Cloud Run Job

```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
gcloud builds submit --tag $REPO/txn-generator .
gcloud run jobs create finchat-dev-generator \
  --image $REPO/txn-generator --region us-central1 \
  --service-account finchat-dev-pipeline@strongsville-city-schools.iam.gserviceaccount.com \
  --args="--count=5000,--project=strongsville-city-schools,--topic=finchat-dev-transactions-ingest"
gcloud run jobs execute finchat-dev-generator --region us-central1
```

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `--count` | 1000 | hard-capped at 10000 |
| `--max-per-customer` | 4 | enforced 1–4 |
| `--overdraft-rate` | 0.05 | fraction forced negative (seeds overdraft_history) |
| `--seed` | none | reproducible runs |
| `--out` / `--dry-run` | — | offline modes |
