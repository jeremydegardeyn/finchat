#!/usr/bin/env bash
# Launch the transactions Dataflow Flex Template (streaming) on-demand (ADR-0003).
# Reads the CI/Terraform-built template at gs://finchat-<env>-dataflow/templates/txn-pipeline.json.
#
# Usage: ./scripts/run_dataflow.sh [dev|test|prod] [dlp] [DRAIN_MINUTES]
#   ./scripts/run_dataflow.sh dev            # launch, leave running
#   ./scripts/run_dataflow.sh dev dlp        # with DLP de-identification
#   ./scripts/run_dataflow.sh dev 15         # launch, auto-drain after 15 min (on-demand)
#   ./scripts/run_dataflow.sh dev dlp 15     # DLP + auto-drain after 15 min
#
# Without a drain time it's a STREAMING job that stays up until you drain it.
# With DRAIN_MINUTES the script waits then drains (run with `&` to background it).
set -euo pipefail

PROJECT="strongsville-city-schools"
REGION="us-central1"
ENV="${1:-dev}"
shift || true
case "$ENV" in dev | test | prod) ;; *) echo "env must be dev|test|prod" >&2; exit 1 ;; esac

WITH_DLP=""
DRAIN_AFTER="" # minutes; empty = leave running
for arg in "$@"; do
  case "$arg" in
    dlp) WITH_DLP="dlp" ;;
    *[!0-9]*) echo "ignoring unknown arg: $arg" >&2 ;;
    *) DRAIN_AFTER="$arg" ;;
  esac
done

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
echo "→ Resolving job id…"
JOB_ID=""
for _ in $(seq 1 12); do
  JOB_ID="$(gcloud dataflow jobs list --region "$REGION" --project "$PROJECT" \
    --filter="name=${JOB}" --format='value(JOB_ID)' 2>/dev/null | head -1)"
  [ -n "$JOB_ID" ] && break
  sleep 5
done
echo "Job: ${JOB_ID:-<pending>}  (name ${JOB})"

if [ -n "$DRAIN_AFTER" ] && [ -n "$JOB_ID" ]; then
  echo "→ Auto-drain in ${DRAIN_AFTER} min. Ctrl-C cancels the wait (the job keeps running)."
  sleep $((DRAIN_AFTER * 60))
  echo "→ Draining ${JOB_ID}…"
  gcloud dataflow jobs drain "$JOB_ID" --region "$REGION" --project "$PROJECT"
  echo "✓ Drain requested — workers stop after in-flight work finishes (idle cost → ~0)."
else
  echo "Status: gcloud dataflow jobs list --region ${REGION} --filter='name:${JOB}'"
  echo "Drain : gcloud dataflow jobs drain ${JOB_ID:-<JOB_ID>} --region ${REGION}   # stop billing when done"
fi
