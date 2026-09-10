# ADR-0032 — AWS workload federation for the agent channel

- **Status:** Accepted (applied in dev and prod; the AWS-side function is not yet deployed)
- **Date:** 2026-09-09
- **Deciders:** Principal Data Architect
- **Context tags:** Cross-cloud identity, non-human identity, MCP, workload federation

## Context

The MCP endpoint ([ADR-0031](0031-mcp-over-http-service-identity.md)) is public and
enforces its own authentication, and it already admits two kinds of caller: a **person**
holding a token from the OAuth proxy ([ADR-0020](0020-remote-mcp-workspace-federation.md)),
and a **service** in GCP holding a Google OIDC token audienced to the service.

A container running in AWS is neither. It needed a way in, and the obvious two are both
wrong:

**OAuth is wrong for a workload.** The flow is authorization-code with PKCE and needs a
person at the Google consent step. It can be run once by hand and the refresh token kept
— and it works until the container restarts. Refresh tokens here **rotate**, so each use
invalidates the previous one; a workload that does not durably persist the newest token
locks itself out. The failure presents as an integration that passed testing and died
overnight, which is the most expensive shape a credential bug can take.

**A service-account key is wrong for a bank.** It is a long-lived secret that must be
distributed to another cloud provider, stored, and rotated. This platform has no
service-account keys anywhere, and adding the first one for a demo harness would be a
poor trade.

## Decision

Federate an **AWS IAM role** to a dedicated GCP service account using **Workload Identity
Federation**, and let the existing service-caller path do the rest.

```
AWS IAM role --(signed sts:GetCallerIdentity)--> GCP STS
     |                                              |
     |        impersonates finchat-<env>-aws-mcp (zero project roles)
     |                                              |
     +---> Google OIDC token, aud = the MCP service URL
                              |
                    finchat-<env>-mcp validates it as a SERVICE caller
```

**No MCP server code changed.** The AWS caller arrives on exactly the code path the AI
gateway already uses; the only additions are Terraform and a name in
`FINCHAT_MCP_SERVICE_CALLERS`.

Four decisions inside that are worth recording, because each has a plausible alternative
that is worse:

### 1. A separate pool per environment, not the CI pool

`finchat-gh-pool` is CI's lifeline: its provider trusts GitHub's issuer, and it is the
pool that deploys the platform. An AWS provider has different attribute mapping and a
different blast radius. Adding a new external trust relationship inside the pool that
holds deploy access, to save creating one resource, is not a saving.

### 2. An attribute condition pinning one role ARN

```hcl
attribute_condition = "attribute.aws_role == \"arn:aws:sts::${account}:assumed-role/${role}\""
```

Without it, **any** principal in the AWS account can impersonate the service account. An
AWS account is an *authentication* boundary, not an *authorization* one, and the two are
easy to conflate — the provider is already scoped to one account, which reads as though
the work is done.

### 3. A different role name per environment

`finchat-dev-mcp-client` and `finchat-prod-mcp-client`, in one AWS account. Since the
condition pins a single ARN, distinct names are what stop whatever assumed the dev role
from reaching prod. Reusing one name across environments would make the environment
boundary decorative.

### 4. The credential configuration omits `--service-account`

With it, ADC *is* the service account, so minting an id-token means impersonating itself
— which needs `roles/iam.serviceAccountTokenCreator` on itself. Without it, ADC is the
federated principal, which already holds `roles/iam.workloadIdentityUser` on the account,
and that role includes `iam.serviceAccounts.getOpenIdToken`. One fewer grant, same result.

## Consequences

### An AWS caller is a service, and gets no persona

`mcp_server/caller.py` will not resolve a persona for a service. That is deliberate: a
persona is a human role, column-level security under
[ADR-0019](0019-end-user-credential-propagation.md) evaluates against a *person*, and
handing a machine one would put a human in the audit trail who did nothing.

The entitlement instead comes from the **agent registry**
([ADR-0023](0023-agent-registry-and-identity.md)), where `aws_mcp_harness` carries a named
accountable owner, a recertification date the build enforces, and a tool allow-list CI
checks. Governance rather than impersonation. See
[docs/29](../29-mcp-from-aws.md) for the operational runbook.

### The network path is not the control

Traffic reaches a public Google endpoint over TLS. There is no VPC peering between AWS and
GCP — that is an intra-cloud concept — and the alternatives (HA VPN, Partner Interconnect)
cost from roughly $110/month upward and, on their own, do not even give private
reachability to a Cloud Run service without a Private Service Connect endpoint.

They buy latency predictability, bandwidth commitments, lower egress rates at volume, or
compliance with a written standard. They do **not** make the call safer: authentication,
authorization, screening and audit are enforced at the application layer and are identical
either way. Buying private transport for the security it does not add is a common and
expensive mistake, so the reason should be named explicitly whenever it is bought.

### `fetch_id_token` does not work here, and says something misleading

`google.oauth2.id_token.fetch_id_token` raises *"Neither metadata server or valid service
account credentials are found"* for a WIF credential configuration that is present and
correct. Its source handles `service_account` and `impersonated_service_account` files and
the metadata server; `external_account` is none of those. Call `generateIdToken`
explicitly instead — `scripts/mcp_client_example.py` and `aws/mcp-harness/handler.py` both
do, with the reason in a comment, because this message has now sent three different
investigations toward permissions.

### Lambda has no metadata service, and federation still works

Most WIF documentation assumes an EC2 metadata endpoint. google-auth checks
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` before trying IMDS and
reads `AWS_REGION` the same way — its own source cites Lambda as the reason. Lambda sets
all four for the execution role, so the identity that federates is the execution role and
nothing is stored.

This is what makes the harness free: Lambda's perpetual free tier covers it, where App
Runner (the Cloud Run analogue) has no scale-to-zero and bills provisioned memory while
idle.

## Alternatives considered

| Option | Why not |
|---|---|
| OAuth device flow | Still needs a human, and the token still rotates. |
| Service-account key in AWS Secrets Manager | A long-lived secret on another cloud. The platform has none today. |
| Reuse `finchat-gh-pool` | Puts a new external trust inside the pool that deploys everything. |
| Pin on AWS account only, no role condition | Every principal in the account could impersonate the service account. |
| mTLS with a client certificate | A second PKI to run, and it authenticates a certificate rather than a workload. |
| Private Interconnect first | Solves a problem we do not have yet, at recurring cost, and does not replace any of the above. |

## Open

**Acting as a person from AWS.** A WIF caller is a service, permanently. If a workload
ever needs a staff surface, the honest fix is a person's token, not widening what a service
may be — which lands on the same unresolved question as the agent channel generally: an
end-user token exchange for tools, still to be decided.
