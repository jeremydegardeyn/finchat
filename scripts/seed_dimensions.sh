#!/usr/bin/env bash
# Backfill customer/account dimensions from observed transactions so the Gold
# balance/summary views return data. Idempotent. Run after data has landed in
# silver.transaction.  Usage (from repo root): ./scripts/seed_dimensions.sh [dev|test|prod]
set -euo pipefail

PROJECT="strongsville-city-schools"
ENV="${1:-dev}"
SQL="products/transactions/schemas/seed_dimensions.sql"

echo "→ Seeding customer/account dimensions for ${ENV} from silver.transaction"
sed -e "s/\${PROJECT}/${PROJECT}/g" -e "s/\${ENV}/${ENV}/g" "$SQL" \
  | bq query --project_id="$PROJECT" --use_legacy_sql=false

echo "✓ Done. Row counts:"
bq query --project_id="$PROJECT" --use_legacy_sql=false --format=prettyjson \
  "SELECT
     (SELECT COUNT(*) FROM \`${PROJECT}.finchat_silver_${ENV}.customer\`) AS customers,
     (SELECT COUNT(*) FROM \`${PROJECT}.finchat_silver_${ENV}.account\`)  AS accounts,
     (SELECT COUNT(*) FROM \`${PROJECT}.finchat_silver_${ENV}.transaction\`) AS transactions"
