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
#
# A drain is not the end of billing. The pipeline's DeduplicatePerKey holds a
# processing-time timer per idempotency key for --dedup_ttl_seconds (3600 by
# default), and a drain waits for outstanding timers, so a job that saw its last
# message at minute 14 stays DRAINING — with workers — until minute 74. The data
# is not what those timers hold: every element was written on arrival, the state
# is only the dedup memory. So after the drain request the script gives in-flight
# work DRAIN_GRACE_MINUTES (default 5) to finish, then cancels what is left.
set -euo pipefail
DRAIN_GRACE_MINUTES="${DRAIN_GRACE_MINUTES:-5}"

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
  # The templates are regional (ADR-0026); the pipeline's DLP request parent has to match.
  DLP_LOC="$(terraform -chdir="infra/envs/${ENV}" output -raw dlp_location)"
  PARAMS="${PARAMS},deid_template=${DEID},inspect_template=${INSPECT},dlp_sample_rate=0.2,dlp_location=${DLP_LOC}"
  echo "→ DLP de-identification ENABLED"
fi

job_state() {
  gcloud dataflow jobs describe "$1" --region "$REGION" --project "$PROJECT" \
    --format='value(currentState)' 2>/dev/null || true
}

echo "→ Launching ${JOB}"
# The launch response carries the job id, so take it from there. The previous
# version polled `jobs list` by name for 60 s; a job that sits in QUEUED longer
# than that (they do) came back empty, the script printed "<pending>" and
# exited, and the auto-drain it promised never armed — a streaming job left
# running on a project that is meant to idle at ~$0 (ADR-0003).
JOB_ID="$(gcloud dataflow flex-template run "$JOB" \
  --template-file-gcs-location "gs://${BUCKET}/templates/txn-pipeline.json" \
  --region "$REGION" --project "$PROJECT" \
  --temp-location "gs://${BUCKET}/temp" \
  --staging-location "gs://${BUCKET}/staging" \
  --service-account-email "$SA" \
  --parameters "$PARAMS" \
  --format='value(job.id)')"

if [ -z "$JOB_ID" ]; then
  # Belt and braces: the launch succeeded (set -e) but the response shape was not
  # what we expected. Fall back to the by-name lookup, for long enough this time.
  echo "→ Launch response carried no job id; resolving by name (up to 5 min)…"
  for _ in $(seq 1 60); do
    JOB_ID="$(gcloud dataflow jobs list --region "$REGION" --project "$PROJECT" \
      --filter="name=${JOB}" --format='value(id)' 2>/dev/null | head -1)"
    [ -n "$JOB_ID" ] && break
    sleep 5
  done
fi
echo "Job: ${JOB_ID:-<unresolved>}  (name ${JOB})"

if [ -n "$DRAIN_AFTER" ] && [ -z "$JOB_ID" ]; then
  # Never exit 0 having silently dropped the one thing the caller asked for.
  echo "!! auto-drain NOT armed: could not resolve the job id. Stop it by hand:" >&2
  echo "   gcloud dataflow jobs list --region ${REGION} --filter='name:${JOB}'" >&2
  exit 1
fi

if [ -n "$DRAIN_AFTER" ]; then
  echo "→ Auto-drain in ${DRAIN_AFTER} min. Ctrl-C cancels the wait (the job keeps running)."
  sleep $((DRAIN_AFTER * 60))
  # A job can sit QUEUED/PENDING for 5+ minutes; drain is only accepted on a
  # RUNNING job. Wait for it (bounded) rather than fail the one call that matters.
  for _ in $(seq 1 40); do
    STATE="$(job_state "$JOB_ID")"
    [ "$STATE" = "JOB_STATE_RUNNING" ] && break
    case "$STATE" in JOB_STATE_DONE|JOB_STATE_CANCELLED|JOB_STATE_FAILED|JOB_STATE_DRAINED) break ;; esac
    echo "  job is ${STATE:-unknown}; waiting for RUNNING before draining…"
    sleep 15
  done
  echo "→ Draining ${JOB_ID}…"
  gcloud dataflow jobs drain "$JOB_ID" --region "$REGION" --project "$PROJECT"

  # Drain waits for the dedup timers (see header). Give real in-flight work a
  # grace period, then cancel: the elements are already in BigQuery, only the
  # dedup state is left, and that is worth nothing on a job that is ending.
  echo "→ Waiting up to ${DRAIN_GRACE_MINUTES} min for DRAINED…"
  STATE=""
  for _ in $(seq 1 $((DRAIN_GRACE_MINUTES * 4))); do
    STATE="$(job_state "$JOB_ID")"
    case "$STATE" in JOB_STATE_DRAINED|JOB_STATE_DONE|JOB_STATE_CANCELLED|JOB_STATE_FAILED) break ;; esac
    sleep 15
  done
  if [ "$STATE" = "JOB_STATE_DRAINED" ]; then
    echo "✓ Drained — workers gone (idle cost → ~0)."
  else
    echo "→ Still ${STATE:-unknown} after ${DRAIN_GRACE_MINUTES} min (dedup timers); cancelling…"
    gcloud dataflow jobs cancel "$JOB_ID" --region "$REGION" --project "$PROJECT"
    for _ in $(seq 1 40); do
      STATE="$(job_state "$JOB_ID")"
      [ "$STATE" = "JOB_STATE_CANCELLED" ] && break
      sleep 15
    done
    if [ "$STATE" = "JOB_STATE_CANCELLED" ]; then
      echo "✓ Cancelled — workers gone (idle cost → ~0)."
    else
      echo "!! job is ${STATE:-unknown}, not CANCELLED. Check it:" >&2
      echo "   gcloud dataflow jobs describe ${JOB_ID} --region ${REGION}" >&2
      exit 1
    fi
  fi
else
  echo "Status: gcloud dataflow jobs list --region ${REGION} --filter='name:${JOB}'"
  echo "Drain : gcloud dataflow jobs drain ${JOB_ID:-<JOB_ID>} --region ${REGION}   # stop billing when done"
fi
