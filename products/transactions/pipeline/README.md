# Transactions Streaming Pipeline (Apache Beam / Dataflow)

`Pub/Sub → parse + schema enforcement → DLP de-identify (sampled) → enrich → BigQuery Silver`,
with malformed messages routed to a dead-letter queue. Packaged as a **Flex Template** and run
**on-demand** by default (ADR-0003) to keep idle cost ~$0.

## Test offline (DirectRunner, no GCP)

```bash
pip install "apache-beam[gcp]"
python ../generator/generate.py --count 50 --dry-run > in.jsonl
python pipeline.py --input_file in.jsonl --output_file valid --dlq_file dlq
# -> valid-*.jsonl (records bound for Silver), dlq-*.jsonl (rejects + reasons)

pytest test_transforms.py          # pure transform unit tests (10 cases)
```

## Build the Flex Template

```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
BUCKET=finchat-dev-dataflow
gcloud builds submit --tag $REPO/txn-pipeline .
gcloud dataflow flex-template build gs://$BUCKET/templates/txn-pipeline.json \
  --image $REPO/txn-pipeline --sdk-language PYTHON --metadata-file metadata.json
```

## Run on-demand (sandbox — drains after)

```bash
gcloud dataflow flex-template run "txn-stream-$(date +%s)" \
  --template-file-gcs-location gs://$BUCKET/templates/txn-pipeline.json \
  --region us-central1 \
  --service-account-email finchat-dev-pipeline@strongsville-city-schools.iam.gserviceaccount.com \
  --parameters input_subscription=projects/strongsville-city-schools/subscriptions/finchat-dev-transactions-dataflow,\
output_table=strongsville-city-schools:finchat_silver_dev.transaction,\
dlq_topic=projects/strongsville-city-schools/topics/finchat-dev-transactions-dlq
```

For the **enterprise** 24/7 streaming job, set `enable_streaming_job = true` in Terraform — same
template, different lifetime.

## Design notes

- **Schema enforcement / validation** in `transforms.parse_and_validate` (Beam-free, unit-tested).
- **DLQ**: `ParseAndValidate` is a tagged-output DoFn; failures carry the error reason + raw payload.
- **Dedup**: BigQuery streaming `insertId = idempotency_key` gives best-effort exactly-once; a
  scheduled `MERGE` from Bronze provides the durable guarantee.
- **DLP**: sampled de-identification (`--dlp_sample_rate`) keeps sandbox cost near zero.
- **Lineage**: `enrich` stamps `ingest_time`, `source_system`, `pipeline_version` on every row.
