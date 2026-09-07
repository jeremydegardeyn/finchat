#!/usr/bin/env bash
# Apply products/loans/schemas/ddl.sql to one environment.
#
# This exists because nothing applied it. The DDL has been in the repo since Increment 3
# and was run by hand, once, per environment — so when `GET /v1/loans` gained an
# `account_id` filter and the `loan_status` view gained the column to match, the file
# changed and no database did. The filter returned `400 Unrecognized name: account_id`
# on every deployed call in all three environments for four weeks, while the tests
# passed against an in-memory dict and the process layer degraded quietly to
# `partial: ["loans"]` — which reads as "this customer has no loans".
#
# Every statement is idempotent (CREATE ... IF NOT EXISTS, ALTER ... ADD COLUMN IF NOT
# EXISTS, CREATE OR REPLACE VIEW) and none of them touch data, so running it repeatedly
# is safe and is the point.
#
# Deliberately NOT a step in build-deploy.yml. The CI/CD service account holds
# `roles/viewer` and `roles/bigquery.jobUser` so that `terraform plan` can refresh state;
# it has never held write access to a dataset, and giving it dataEditor to apply a view
# would trade a schema-drift bug for a much larger blast radius. Schema is Terraform-time
# work, so run this next to `terraform apply`, from the same seat.
#
#   scripts/apply_loans_ddl.sh dev
#   scripts/apply_loans_ddl.sh prod --dry-run
set -euo pipefail

ENV="${1:?usage: apply_loans_ddl.sh <dev|test|prod> [--dry-run]}"
DRY="${2:-}"
PROJECT="${GCP_PROJECT:-strongsville-city-schools}"
LOCATION="${BQ_LOCATION:-us-central1}"
DDL="$(cd "$(dirname "$0")/.." && pwd)/products/loans/schemas/ddl.sql"
# `python3` is not on PATH on Windows, where this is also run.
PY="$(command -v python3 || command -v python)"

case "$ENV" in
  dev|test|prod) ;;
  *) echo "unknown environment: $ENV" >&2; exit 2 ;;
esac

rendered="$(sed -e "s/\${PROJECT}/${PROJECT}/g" -e "s/\${ENV}/${ENV}/g" "$DDL")"

if [ "$DRY" = "--dry-run" ]; then
  echo "$rendered"
  exit 0
fi

echo "── Applying loans DDL to ${PROJECT}.finchat_loans_${ENV} (${LOCATION})"
# --location matters: a dataset of the same name can exist in another location, and bq
# will happily resolve that one instead. The AI gateway's audit table was edited in the
# wrong dataset for exactly this reason.
printf '%s\n' "$rendered" | bq query \
  --project_id="$PROJECT" --location="$LOCATION" --use_legacy_sql=false --quiet

echo "── Verifying the view exposes what the API filters on"
bq show --project_id="$PROJECT" --format=prettyjson "finchat_loans_${ENV}.loan_status" \
  | "$PY" -c '
import json, sys
cols = {f["name"] for f in json.load(sys.stdin)["schema"]["fields"]}
need = {"loan_id", "status", "account_id", "submitted_at"}
missing = sorted(need - cols)
if missing:
    sys.exit(f"loan_status is missing {missing} — the API filters on these")
print("   loan_status projects:", ", ".join(sorted(cols)))
'
