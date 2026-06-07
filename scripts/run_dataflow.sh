#!/usr/bin/env bash
# Launch the transactions Dataflow Flex Template (streaming) on-demand (ADR-0003).
# Reads the CI/Terraform-built template at gs://finchat-<env>-dataflow/templates/txn-pipeline.json.
#
# Usage: ./scripts/run_dataflow.sh [dev|test|prod] [dlp]
#   ./scripts/run_dataflow.sh dev          # no DLP de-identification
#   ./scripts/run_dataflow.sh dev dlp      # with DLP (pulls templates from terraform output)
#
# It's a STREAMING job — it stays up until you drain it (see hint at the end).
set -euo pipefail

PROJECT="strongsville-city-schools"
REGION="us-central1"
ENV="${1:-dev}"
WITH_DLP="${2:-}"
case "$ENV" in dev | test | prod) ;; *) echo "env must be dev|test|prod" >&2; exit 1 ;; esac

BUCKET="finchat-${ENV}-dataflow"
SA="finchat-${ENV}-pipeline@${PROJECT}.iam.gserviceaccount.com"
JOB="txn-stream-${ENV}-$(date +%s)"

PARAMS="input_subscription=projects/${PROJECT}/subscriptions/finchat-${ENV}-transactions-dataflow"
PARAMS="${PARAMS},output_table=${PROJECT}:finchat_silver_${ENV}.transaction"
PARAMS="${PARAMS},dlq_topic=projects/${PROJECT}/topics/finchat-${ENV}-transactions-dlq"

if [ "$WITH_DLP" = "dlp" ]; then
  DEID="$(terraform -chdir="infra/envs/${ENV}" output -raw dlp_deidentify_template)"
  INSPECT="$(terraform -chdir="infra/envs/${ENV}" output -raw dlp_inspect_template)"
  PARAMS="${PARAMS},deid_template=${DEID},inspect_template=${INSPECT},dlp_sample_rate=0.2"
  echo "→ DLP de-identification ENABLED"
fi

echo "→ Launching ${JOB}"
gcloud dataflow flex-template run "$JOB" \
  --template-file-gcs-location "gs://${BUCKET}/templates/txn-pipeline.json" \
  --region "$REGION" --project "$PROJECT" \
  --temp-location "gs://${BUCKET}/temp" \
  --staging-location "gs://${BUCKET}/staging" \
  --service-account-email "$SA" \
  --parameters "$PARAMS"

echo
echo "Status: gcloud dataflow jobs list --region ${REGION} --filter='name:${JOB}'"
echo "Drain : gcloud dataflow jobs drain <JOB_ID> --region ${REGION}   # stop billing when done"
