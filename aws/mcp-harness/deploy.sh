#!/usr/bin/env bash
# Deploy the MCP harness Lambda. Run from the repo root, with an AWS profile that can
# create IAM roles, ECR repositories and Lambda functions.
#
#   AWS_ACCOUNT_ID=123456789012 ENV=dev ./aws/mcp-harness/deploy.sh
#
# Idempotent: re-running updates the function rather than failing on a name collision.
# Everything it creates sits inside the free tier except the ECR image (~$0.03/month).
set -euo pipefail

: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID (12 digits)}"
ENV="${ENV:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GCP_PROJECT="${GCP_PROJECT:-strongsville-city-schools}"
ROLE_NAME="${ROLE_NAME:-finchat-${ENV}-mcp-client}"
FUNCTION="${FUNCTION:-finchat-${ENV}-mcp-harness}"
REPO="${REPO:-finchat-mcp-harness}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# The ROLE NAME must match `aws_mcp_client_role` in the environment's terraform.tfvars.
# The pool's attribute condition pins exactly this ARN; a mismatch fails at the token
# exchange with "unable to acquire impersonated credentials" and nothing more specific.
SA="finchat-${ENV}-aws-mcp@${GCP_PROJECT}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "${GCP_PROJECT}" --format='value(projectNumber)')"
MCP_URL="$(gcloud run services describe "finchat-${ENV}-mcp" --region us-central1 \
  --format='value(status.url)')/mcp"

echo "==> Execution role ${ROLE_NAME}"
# No AWS permissions beyond writing its own logs. This role is an IDENTITY TO PROVE, not
# a set of entitlements — everything it may do lives on the Google side.
if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam create-role --role-name "${ROLE_NAME}" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  aws iam attach-role-policy --role-name "${ROLE_NAME}" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "    created; waiting for IAM propagation"
  sleep 10
else
  echo "    exists"
fi

echo "==> Credential configuration"
# No --service-account on purpose. With it, ADC would BE the service account, and minting
# an id-token would mean impersonating itself — which needs serviceAccountTokenCreator on
# itself, an extra grant for nothing. Without it, ADC is the federated principal, which
# already holds workloadIdentityUser (and therefore getOpenIdToken) on the account.
gcloud iam workload-identity-pools create-cred-config \
  "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/finchat-${ENV}-aws-pool/providers/finchat-${ENV}-aws-provider" \
  --aws \
  --output-file="${HERE}/aws-credentials.json" \
  --quiet

echo "==> Image"
aws ecr describe-repositories --repository-names "${REPO}" --region "${AWS_REGION}" \
  >/dev/null 2>&1 || aws ecr create-repository --repository-name "${REPO}" \
  --region "${AWS_REGION}" >/dev/null
REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"
# Lambda runs x86_64 unless told otherwise; an arm64 laptop silently builds an image the
# function cannot start, and the error is a runtime exit rather than a deploy failure.
docker build --platform linux/amd64 -t "${REGISTRY}/${REPO}:${ENV}" "${HERE}"
docker push "${REGISTRY}/${REPO}:${ENV}"

echo "==> Function ${FUNCTION}"
ENV_VARS="Variables={FINCHAT_MCP_URL=${MCP_URL},FINCHAT_MCP_SERVICE_ACCOUNT=${SA}}"
if aws lambda get-function --function-name "${FUNCTION}" --region "${AWS_REGION}" \
    >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "${FUNCTION}" \
    --image-uri "${REGISTRY}/${REPO}:${ENV}" --region "${AWS_REGION}" >/dev/null
  aws lambda wait function-updated --function-name "${FUNCTION}" --region "${AWS_REGION}"
  aws lambda update-function-configuration --function-name "${FUNCTION}" \
    --timeout 60 --memory-size 512 --environment "${ENV_VARS}" \
    --region "${AWS_REGION}" >/dev/null
else
  aws lambda create-function --function-name "${FUNCTION}" \
    --package-type Image --code "ImageUri=${REGISTRY}/${REPO}:${ENV}" \
    --role "arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}" \
    --timeout 60 --memory-size 512 --environment "${ENV_VARS}" \
    --region "${AWS_REGION}" >/dev/null
fi
aws lambda wait function-updated --function-name "${FUNCTION}" --region "${AWS_REGION}"

echo "==> Invoking"
# No function URL. This is a harness, and a public HTTPS endpoint is a new attack surface
# for something only you invoke. `aws lambda create-function-url-config --auth-type AWS_IAM`
# adds one later if a demo needs to be clicked rather than run.
aws lambda invoke --function-name "${FUNCTION}" --region "${AWS_REGION}" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tool": "finchat_status"}' /dev/stdout
