#!/usr/bin/env bash
# Bootstrap remote Terraform state buckets (run once per environment).
# Usage: ./scripts/bootstrap_state.sh <project_id> [region]
set -euo pipefail

PROJECT="${1:?usage: bootstrap_state.sh <project_id> [region]}"
REGION="${2:-us-central1}"

gcloud config set project "$PROJECT"

for env in dev test prod; do
  BUCKET="finchat-${env}-tfstate"
  if gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
    echo "✓ gs://${BUCKET} already exists"
  else
    echo "→ creating gs://${BUCKET}"
    gcloud storage buckets create "gs://${BUCKET}" \
      --project="$PROJECT" \
      --location="$REGION" \
      --uniform-bucket-level-access
    # Versioning so state is recoverable.
    gcloud storage buckets update "gs://${BUCKET}" --versioning
  fi
done

echo "Done. Now: cd infra/envs/<env> && terraform init"
