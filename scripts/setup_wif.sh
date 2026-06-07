#!/usr/bin/env bash
# Configure Workload Identity Federation so GitHub Actions authenticates to GCP
# with NO service-account keys. Run once per project.
#
# Usage: ./scripts/setup_wif.sh <project_id> <github_owner/repo> [env]
#   e.g. ./scripts/setup_wif.sh strongsville-city-schools jeremydegardeyn/finchat dev
set -euo pipefail

PROJECT="${1:?project_id}"; REPO="${2:?owner/repo}"; ENV="${3:-dev}"
POOL="finchat-gh-pool"; PROVIDER="finchat-gh-provider"
SA="finchat-${ENV}-cicd@${PROJECT}.iam.gserviceaccount.com"
NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"

gcloud config set project "$PROJECT"
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com

# Pool + OIDC provider for GitHub, restricted to your repository.
gcloud iam workload-identity-pools create "$POOL" --location=global \
  --display-name="FinChat GitHub pool" 2>/dev/null || true
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'" 2>/dev/null || true

# Let the GitHub repo impersonate the CI/CD service account.
MEMBER="principalSet://iam.googleapis.com/projects/${NUM}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser --member="$MEMBER"

echo
echo "Set these GitHub Actions variables (per environment):"
echo "  WIF_PROVIDER = projects/${NUM}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  DEPLOY_SA    = ${SA}"
echo "  GCP_PROJECT  = ${PROJECT}"
echo "  GCP_REGION   = us-central1"
