#!/usr/bin/env bash
# Deploy the MCP harness Lambda. Run from the repo root, with an AWS profile that can
# create IAM roles, ECR repositories and Lambda functions.
#
#   ./aws/mcp-harness/deploy.sh              # account read from your AWS credentials
#   ./aws/mcp-harness/deploy.sh 123456789012 # or name it
#
# Idempotent: re-running updates the function rather than failing on a name collision.
# Everything it creates sits inside the free tier except the ECR image (~$0.03/month).
set -euo pipefail

# NOTE: do not `export MSYS_NO_PATHCONV=1` here. It looks like the right defence against
# Git Bash rewriting Unix-looking arguments, and it breaks gcloud, whose Windows wrapper
# DEPENDS on that conversion — it builds a doubled prefix like `C:\c\Users\...` and fails
# on a path that does not exist. Nothing in this script needs it: the one argument that
# did (`/dev/stdout`) is gone, and MSYS converting a mktemp path on the way into aws.exe
# is correct, since aws.exe is a native binary that wants a Windows path.
missing=""
command -v aws >/dev/null 2>&1 || missing="${missing}
  aws     — AWS CLI v2. Windows:  msiexec /i https://awscli.amazonaws.com/AWSCLIV2.msi
            macOS:  brew install awscli     Linux: see docs.aws.amazon.com/cli
            Then open a NEW shell, or: export PATH=\"\$PATH:/c/Program Files/Amazon/AWSCLIV2\""
command -v docker >/dev/null 2>&1 || missing="${missing}
  docker  — Docker Desktop, for building the Lambda image."
command -v gcloud >/dev/null 2>&1 || missing="${missing}
  gcloud  — Google Cloud SDK, to read the pool and mint the credential configuration."
if [ -n "${missing}" ]; then
  echo "Missing tools:${missing}" >&2
  exit 1
fi

# The binary existing is not the daemon running, and `docker build` fails several steps
# later with a message about a pipe that does not name Docker Desktop.
docker info >/dev/null 2>&1 || {
  echo "Docker is installed but not running — start Docker Desktop and retry." >&2
  exit 1
}

# Credentials must exist before anything else, because `aws sts get-caller-identity` is
# how the account is discovered two lines below. Configure them yourself — with SSO
# (`aws configure sso`, short-lived) or an IAM access key (`aws configure`).
aws sts get-caller-identity >/dev/null 2>&1 || {
  echo "AWS credentials are not configured. Run 'aws configure sso' (preferred) or" >&2
  echo "'aws configure' with an IAM access key, then retry." >&2
  exit 1
}

# The account comes from the credentials in use, not from something you have to remember
# to export. `AWS_ACCOUNT_ID=x ./deploy.sh` works; a bare `AWS_ACCOUNT_ID=x` on its own
# line does NOT reach a child process, which is a confusing way to fail.
DISCOVERED="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
AWS_ACCOUNT_ID="${1:-${AWS_ACCOUNT_ID:-${DISCOVERED}}}"
if [ -z "${AWS_ACCOUNT_ID}" ]; then
  echo "No AWS account. Pass it as an argument, or configure AWS credentials." >&2
  exit 1
fi
case "${AWS_ACCOUNT_ID}" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
  *) echo "AWS_ACCOUNT_ID must be 12 digits, got '${AWS_ACCOUNT_ID}'" >&2; exit 1 ;;
esac
# Deploying into an account other than the one you are authenticated to would create the
# role somewhere else and fail later, at the token exchange, with a message about the
# attribute condition — a long way from the cause.
if [ -n "${DISCOVERED}" ] && [ "${DISCOVERED}" != "${AWS_ACCOUNT_ID}" ]; then
  echo "Refusing: your credentials are for ${DISCOVERED}, not ${AWS_ACCOUNT_ID}." >&2
  exit 1
fi
echo "AWS account ${AWS_ACCOUNT_ID}"

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
OUT="$(mktemp)"
aws lambda invoke --function-name "${FUNCTION}" --region "${AWS_REGION}" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tool": "finchat_status"}' "${OUT}" >/dev/null
cat "${OUT}"; echo
rm -f "${OUT}"

# The region is the thing people get wrong next: the function is wherever THIS script put
# it, which is not necessarily the CLI's default. Hand back a command that cannot miss.
cat <<EOF

Deployed ${FUNCTION} in ${AWS_REGION}. Invoke it with the region spelled out:

  aws lambda invoke --region ${AWS_REGION} --function-name ${FUNCTION} \\
    --cli-binary-format raw-in-base64-out \\
    --payload '{"tool":"list_sample_accounts","arguments":{"n":3}}' out.json && cat out.json

Or in the Console: Lambda -> ${FUNCTION} (${AWS_REGION}) -> Test tab.
EOF
