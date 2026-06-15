#!/usr/bin/env bash
# Create the Cloud Identity groups referenced as FinChat data-product access-group
# principals. ONLY needed if you want the per-asset IAM grant (on access approval)
# to actually resolve — the access-group governance model itself works without them.
#
# Groups are directory objects (Cloud Identity / Workspace), not project IAM. They
# live in the datadinosaur.com org. After creating, bind IAM with:
#   FINCHAT_BIND_ASSET_IAM=1 python scripts/data_products.py <env>
# (Allow a few minutes for new groups to propagate before binding.)
#
# Prereqs: Cloud Identity provisioned for the domain + caller has Groups Admin.
# Usage (repo root): ./scripts/create_access_groups.sh
set -euo pipefail

ORG="892617109147"          # datadinosaur.com organization id
DOMAIN="datadinosaur.com"

# group-local-part : "Display Name"
# NOTE: do not name this array GROUPS — that's a special bash variable (the
# current user's numeric GIDs) and assignments to it are silently ignored.
GROUP_DEFS=(
  "deposit-analysts:Deposit Analysts"
  "data-science:Data Science"
  "crm-team:CRM Team"
  "risk-analysts:Risk Analysts"
  "collections-team:Collections"
  "underwriting-team:Underwriting"
  "ai-platform:AI Platform"
  "support-agents:Support Agents"
)

for entry in "${GROUP_DEFS[@]}"; do
  local_part="${entry%%:*}"
  display="${entry#*:}"
  email="${local_part}@${DOMAIN}"
  echo "→ ${email}  (${display})"
  if gcloud identity groups create "${email}" \
       --organization="${ORG}" \
       --group-type=security \
       --display-name="${display}" \
       --description="FinChat data-product access group: ${display}" 2>err.log; then
    echo "  created"
  else
    if grep -qiE "already exists|ALREADY_EXISTS|conflict" err.log; then
      echo "  = already exists (ok)"
    else
      echo "  ✗ $(tail -1 err.log)"
    fi
  fi
done
rm -f err.log

cat <<'NEXT'

Next: bind the per-asset IAM (grants the groups bigquery.dataViewer on each
product's table, the role applied on access approval):

  FINCHAT_BIND_ASSET_IAM=1 python scripts/data_products.py prod
NEXT
