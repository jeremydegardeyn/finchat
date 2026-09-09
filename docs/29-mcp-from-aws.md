# Calling the MCP endpoint from AWS

A container in AWS can reach FinChat's MCP endpoint without a Google key and without a
human. This is the runbook, and the reasoning behind the shape.

## Why not OAuth

[ADR-0020](adr/0020-remote-mcp-workspace-federation.md)'s flow is authorization-code with
PKCE: it needs a person at the Google step. You *can* run it once by hand and keep the
refresh token, and it will work — until the container restarts. Refresh tokens here
**rotate**, so each use invalidates the previous one; a workload that does not persist the
newest token locks itself out, and the symptom is an integration that worked in testing
and dies overnight.

OAuth is right for a person. For a workload, the right answer is the one that needs no
secret at all.

## The shape

```
AWS IAM role  --(WIF: signed sts:GetCallerIdentity)-->  GCP
      |                                                  |
      |                        impersonates finchat-<env>-aws-mcp (no project roles)
      |                                                  |
      +--> Google id-token, aud = the MCP service URL ---+
                                   |
                                   v
                    finchat-<env>-mcp validates it as a SERVICE caller
                    (FINCHAT_MCP_SERVICE_CALLERS)
```

No long-lived credential exists anywhere in this picture. The AWS role proves itself with
a signed request GCP verifies against AWS; GCP returns a short-lived token.

**This is the same code path the AI gateway already uses** — the resource server's
service-caller branch. Nothing new was written to support AWS.

## What an AWS caller can and cannot do

It is a **service**, and `mcp_server/caller.py` will not give a service a persona. That is
deliberate rather than a limitation to work around:

| | |
|---|---|
| Every tool the MCP server offers today | **yes** — the grounded, governed set |
| Any surface behind `caller.require_staff` | **no** — a service has no persona, so the gate refuses it |
| Audit attribution | the service account, not a person |

The middle row is a property of the design rather than of a tool that exists yet: nothing
currently calls `require_staff`, and the free-form analytics tool it was written for is
still unbuilt. It is here because it decides what an AWS harness is for — exercising the
grounded tools, not standing in for an analyst.

If an AWS workload later needs a staff surface, the honest fix is a person's token
(OAuth), not widening what a service may be.

## Setup

### 1. GCP side (Terraform)

In `infra/envs/<env>/terraform.tfvars`:

```hcl
enable_aws_mcp_client = true
aws_account_id        = "123456789012"
aws_mcp_client_role   = "finchat-mcp-client"   # the ROLE NAME, not an ARN
```

Then `terraform apply`. This creates a pool **separate from `finchat-gh-pool`** — that one
is CI's lifeline, and an AWS provider has different attribute mapping and a different
blast radius; sharing it would put a new external trust relationship inside the pool that
deploys the platform.

Note the `attribute_condition`: only the named role may federate. An AWS account is an
*authentication* boundary, not an authorization one, and without that condition every
principal in the account could impersonate the service account.

**Then re-sync the tfvars secret** (`gh secret set TFVARS --env <env> < infra/envs/<env>/terraform.tfvars`)
or the next CI apply reverts it. Gitignored tfvars do not tell the repo what is deployed.

### 2. Add the service account as a permitted caller

The MCP service reads `FINCHAT_MCP_SERVICE_CALLERS`, and the deploy workflow sets it from
a **GitHub environment variable of the same name** — editing the Cloud Run revision by
hand works until the next deploy overwrites it. Append the new account to the existing
list:

```bash
gh variable set FINCHAT_MCP_SERVICE_CALLERS --env dev \
  --body "$(gh variable get FINCHAT_MCP_SERVICE_CALLERS --env dev);finchat-dev-aws-mcp@strongsville-city-schools.iam.gserviceaccount.com"
```

Semicolons, not commas — a comma is `gcloud run deploy --set-env-vars`' own delimiter, and
a list of emails separated by commas is parsed as separate variables, failing with
`Bad syntax for dict arg` naming the second email rather than the reason. `auth.py`
accepts either separator so a hand-written comma is not a trap, but the deploy must use
`;`.

**Then redeploy the MCP service.** Environment variables bake at deploy time; setting the
variable alone changes nothing, and the failure looks like a permissions problem rather
than a stale revision. A commit touching only `scripts/` or `docs/` will not trigger the
deploy — the workflow has a path filter — so run it manually:

```bash
gh workflow run build-deploy.yml -f environment=dev
```

### 3. AWS side

An IAM role your container assumes — a task role on ECS, an IRSA role on EKS, an instance
profile on EC2. It needs **no AWS permissions at all**; it is only an identity to prove.
Its name must match `aws_mcp_client_role`.

Download the credential configuration and hand it to the container:

```bash
gcloud iam workload-identity-pools create-cred-config \
  projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/finchat-<env>-aws-pool/providers/finchat-<env>-aws-provider \
  --aws \
  --output-file=/etc/gcp/aws-credentials.json
```

Note the **absence of `--service-account`**. Adding it makes ADC *be* the service account,
and the container then has to impersonate itself to mint an id-token, which needs
`roles/iam.serviceAccountTokenCreator` on itself — a grant, and one more thing to get
wrong. Without it, ADC is the federated principal, which already holds
`roles/iam.workloadIdentityUser` on the service account, and that role includes
`iam.serviceAccounts.getOpenIdToken`. The client names the service account instead.

Then in the container:

```
GOOGLE_APPLICATION_CREDENTIALS=/etc/gcp/aws-credentials.json
```

That file holds **no secret** — it describes how to exchange the AWS identity, not a
credential. It is safe to bake into an image; the AWS role is what must be protected.

### 4. Verify

```bash
python scripts/mcp_client_example.py \
  --url https://finchat-<env>-mcp-....run.app/mcp \
  --service-account finchat-<env>-aws-mcp@<PROJECT>.iam.gserviceaccount.com
```

It mints an id-token audienced to the service, connects over streamable HTTP, lists the
tools this identity is offered and calls `finchat_status` — which reads no customer data,
so it is safe in a CI log.

The same script runs on a laptop with `gcloud` and no `--service-account`, and inside GCP
off the metadata server — but be clear about what a laptop run proves. Your own Google
identity is not in `FINCHAT_MCP_SERVICE_CALLERS`, so against an OAuth-enforcing endpoint
it gets a **401 with a `WWW-Authenticate: Bearer resource_metadata=...` header**, which is
the resource server working correctly. That is still a useful baseline: it proves DNS,
TLS, Cloud Run's public ingress and the RFC 9728 discovery pointer, and it separates
"cannot reach the endpoint" from "the endpoint will not have me". The script prints the
status and that header rather than the SDK's `unhandled errors in a TaskGroup`, which
names nothing.

One thing it does **not** do is call `google.oauth2.id_token.fetch_id_token`, which is the
obvious spelling and fails here: given a WIF credential configuration it raises "Neither
metadata server or valid service account credentials are found", a message about missing
credentials for a file that is present and correct. It handles `service_account` and
`impersonated_service_account` files and the metadata server; an `external_account` file
is none of those.

## The runtime: Lambda, not App Runner

App Runner is the obvious Cloud Run analogue and it fails the near-zero-cost test — no
scale-to-zero, and it bills provisioned memory while idle, roughly $5/month for something
that is asleep most of the time. Fargate has no scale-to-zero at all.

**Lambda's free tier is perpetual** — 1M requests and 400k GB-seconds a month, not a
12-month trial — so a harness that runs on demand costs nothing. The only standing charge
is the ECR image, about $0.03/month; a zip package would be exactly $0 if that matters.

The reason this works at all is worth writing down, because most WIF documentation
assumes a metadata service and **Lambda has none**. google-auth checks
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` before it tries IMDS,
and reads `AWS_REGION` the same way. Its own source says so:

> The AWS metadata server is not available in some AWS environments such as AWS lambda.
> Instead, it is available via environment variable.

Lambda sets all four for the execution role on every invocation, so the identity that
federates is the **execution role** and no credential is stored anywhere.

`aws/mcp-harness/` holds the whole thing: a handler, an image, and a deploy script.

```bash
AWS_ACCOUNT_ID=<your account> ENV=dev ./aws/mcp-harness/deploy.sh
```

It creates the execution role (with **no AWS permissions** beyond writing its own logs —
the role is an identity to prove, not a set of entitlements), generates the credential
configuration, builds and pushes the image, creates or updates the function, and invokes
it once. Re-running updates rather than failing.

Then, for any tool:

```bash
aws lambda invoke --function-name finchat-dev-mcp-harness \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tool": "get_account_balance", "arguments": {"account_id": "ACC001"}}' \
  /dev/stdout
```

Two deliberate omissions. There is **no function URL**: a public HTTPS endpoint is a new
attack surface for something only you invoke, and
`aws lambda create-function-url-config --auth-type AWS_IAM` adds one later if a demo needs
to be clicked rather than run. And the default tool is `finchat_status`, which reads no
customer data — CloudWatch logs outlive the invocation, and account data should not
accumulate there.

Build for **x86_64** (`--platform linux/amd64`, which the script passes). An arm64 laptop
otherwise builds an image the function cannot start, and the symptom is a runtime exit
rather than a deploy failure.

## When it does not work

| Symptom | Cause |
|---|---|
| `401 invalid_token` | The service account is not in `FINCHAT_MCP_SERVICE_CALLERS`, **or it is and the service was not redeployed** |
| `403` from Cloud Run | The endpoint is private in that environment (`mcp_public = false`) — WIF cannot help; Cloud Run refuses before the server runs |
| `Unable to acquire impersonated credentials` | The `attribute_condition` does not match the assumed role's ARN. Check the role NAME, and that the container really assumed it |
| `403 ... getOpenIdToken denied` while minting | The caller is not bound to the service account. From AWS that means the attribute condition did not match; from a laptop it means you personally lack `roles/iam.serviceAccountTokenCreator` on it, which is the correct posture |
| Tools list, a staff-gated tool refuses | Working as designed — see the table above |
