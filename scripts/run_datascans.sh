#!/usr/bin/env bash
# Run the Dataplex Data Quality + Profile scans for an env (docs/12, ADR-0010).
# Run after `terraform apply` with enable_catalog=true creates the scans.
# Usage (repo root): ./scripts/run_datascans.sh [dev|test|prod]
set -euo pipefail

PROJECT="strongsville-city-schools"
REGION="us-central1"
ENV="${1:-dev}"
PREFIX="finchat-${ENV}"

for scan in silver-txn-profile silver-txn-quality; do
  id="${PREFIX}-${scan}"
  echo "→ running datascan ${id}"
  if out=$(gcloud dataplex datascans run "$id" --project="$PROJECT" --location="$REGION" 2>&1); then
    echo "  started: $(echo "$out" | head -1)"
  else
    echo "  (could not start — is enable_catalog=true applied for ${ENV}? error: $(echo "$out" | tail -1))"
  fi
done

echo
echo "Results (DQ score + rule pass/fail):"
echo "  gcloud dataplex datascans jobs list --datascan=${PREFIX}-silver-txn-quality --project=${PROJECT} --location=${REGION}"
echo "  gcloud dataplex datascans describe ${PREFIX}-silver-txn-quality --project=${PROJECT} --location=${REGION} --view=FULL"
echo
echo "To surface the score on the catalog entry's operational aspect, enable result"
echo "publishing on the scan (Dataplex 'Publish to Catalog') or write it via the"
echo "Catalog API in scripts/catalog_bootstrap.py."
