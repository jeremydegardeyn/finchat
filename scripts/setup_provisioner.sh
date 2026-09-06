#!/usr/bin/env bash
# Create the per-environment PROVISIONING service account that `infra.yml` runs as.
#
# Usage: ./scripts/setup_provisioner.sh <project_id> <github_owner/repo> [env]
#   e.g. ./scripts/setup_provisioner.sh strongsville-city-schools jeremydegardeyn/finchat dev
#
# Why this exists (ADR-0029). `finchat-<env>-cicd@` was scoped for build-and-deploy:
# Cloud Run, Artifact Registry, Dataflow, BigQuery jobs. Terraform manages far more
# than that — Pub/Sub, Secret Manager, Eventarc, Workflows, Cloud SQL, Bigtable, DLP,
# Data Catalog, custom IAM roles — so every `terraform apply` failed on a 403, one
# resource further along each time. Granting those roles to the deploy SA would have
# meant the identity that runs on every push to main could also rewrite the project's
# IAM, its secrets and its data policies.
#
# So the privilege goes to a SEPARATE identity used only by `infra.yml`, which is
# gated behind the GitHub Environment's required reviewers. The deploy SA keeps the
# narrow set it always had.
#
# This has to be bootstrapped outside Terraform: it is the identity Terraform runs as,
# so Terraform cannot be the thing that creates it.
set -euo pipefail

PROJECT="${1:?project_id}"; REPO="${2:?owner/repo}"; ENV="${3:-dev}"
POOL="finchat-gh-pool"; PROVIDER="finchat-gh-provider"
NAME="finchat-${ENV}-provisioner"
SA="${NAME}@${PROJECT}.iam.gserviceaccount.com"
NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"

gcloud config set project "$PROJECT"

# Distinguish "already exists" from a real failure. `2>/dev/null || true` hides a genuine
# error and then grants roles to an account that is not there — which is how the first run
# of this script produced a confusing "does not exist" from a completely different command.
if err="$(gcloud iam service-accounts create "$NAME" \
    --display-name="FinChat ${ENV} Terraform provisioner" \
    --description="Runs infra.yml only. Separate from finchat-${ENV}-cicd (ADR-0029)." 2>&1)"; then
  echo "created ${SA}"
elif grep -qiE "already exists|ALREADY_EXISTS" <<<"$err"; then
  echo "service account already exists, continuing"
else
  echo "$err" >&2
  exit 1
fi

# A newly created service account is not immediately visible to the project IAM policy
# API. The first add-iam-policy-binding after a create fails with
#   INVALID_ARGUMENT: Service account ... does not exist
# which is propagation lag, not absence — the account is already listable. With `set -e`
# that aborted this script after creating the account and before granting it anything,
# leaving a provisioner with no roles and no WIF binding. So retry on that one message,
# and only that one.
grant() {
  local role="$1" out
  for _ in 1 2 3 4 5 6 7 8; do
    if out="$(gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:${SA}" --role="$role" --condition=None --quiet 2>&1)"; then
      return 0
    fi
    if grep -q "does not exist" <<<"$out"; then sleep 5; continue; fi
    echo "$out" >&2
    return 1
  done
  echo "gave up on ${role}: the account is still not visible to the IAM API" >&2
  return 1
}

# Every role here is required by a resource type the Terraform actually declares —
# derived from `grep -rhoE '^resource "google_[a-z0-9_]+"' infra/`, not from guesswork,
# so the list can be re-derived rather than accumulating by trial and error.
ROLES=(
  roles/resourcemanager.projectIamAdmin  # google_project_iam_member (12)
  roles/iam.roleAdmin                    # google_project_iam_custom_role
  roles/iam.serviceAccountAdmin          # google_service_account
  roles/iam.serviceAccountUser           # act-as, for services that run as an SA
  roles/serviceusage.serviceUsageAdmin   # google_project_service
  roles/bigquery.admin                   # datasets, tables, jobs, connections, data policies
  roles/datacatalog.admin                # taxonomy, policy tags + their IAM
  roles/pubsub.admin                     # topics, subscriptions, schema, topic IAM
  roles/secretmanager.admin              # secrets, versions, secret IAM
  roles/eventarc.admin                   # google_eventarc_trigger
  roles/workflows.admin                  # google_workflows_workflow
  roles/cloudscheduler.admin             # google_cloud_scheduler_job
  roles/run.admin                        # services, domain mapping, service IAM
  roles/logging.configWriter             # project sinks + bucket config
  roles/monitoring.editor                # alert policy + notification channel
  roles/bigtable.admin                   # instance, tables, gc policies, instance IAM
  roles/cloudsql.admin                   # database instance, database, user
  roles/dlp.admin                        # inspect + de-identify templates
  roles/dataplex.admin                   # entry group, aspect type, datascans
  roles/dataflow.admin                   # google_dataflow_flex_template_job
  roles/storage.admin                    # buckets (incl. the Terraform state bucket)
  roles/artifactregistry.admin           # google_artifact_registry_repository
  roles/apigateway.admin                 # api, api config, gateway
  roles/modelarmor.admin                 # template + floor setting
)

for role in "${ROLES[@]}"; do
  grant "$role"
  echo "  granted ${role}"
done

# The billing budget lives on the BILLING ACCOUNT, not the project, so it is a
# separate grant at a different scope. Skipped unless BILLING_ACCOUNT is exported,
# because that account may fund projects beyond this one and is often org-administered.
if [[ -n "${BILLING_ACCOUNT:-}" ]]; then
  gcloud billing accounts add-iam-policy-binding "$BILLING_ACCOUNT" \
    --member="serviceAccount:${SA}" --role="roles/billing.costsManager" >/dev/null
  echo "  granted roles/billing.costsManager on ${BILLING_ACCOUNT}"
else
  echo "  SKIPPED roles/billing.costsManager — export BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX to grant it"
fi

# Let the GitHub repo impersonate this account, same pool/provider as the deploy SA.
MEMBER="principalSet://iam.googleapis.com/projects/${NUM}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser --member="$MEMBER" >/dev/null
echo "  bound ${REPO} via workload identity"

cat <<EOF

Done. Set this GitHub Actions variable on the '${ENV}' environment:

  PROVISION_SA = ${SA}

  gh variable set PROVISION_SA --env ${ENV} --repo ${REPO} --body "${SA}"

Then remove the escalation the deploy SA no longer needs:

  gcloud projects remove-iam-policy-binding ${PROJECT} \\
    --member="serviceAccount:finchat-${ENV}-cicd@${PROJECT}.iam.gserviceaccount.com" \\
    --role="roles/resourcemanager.projectIamAdmin"
EOF
