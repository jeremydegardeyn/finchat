# ADR-0031 — Deploy the MCP server over HTTP, and authenticate it with Cloud Run IAM

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Principal Cloud Architect
- **Context tags:** MCP, remote tool servers, service identity, OIDC, agent channel

## Context

[ADR-0028](0028-mcp-as-the-agent-channel.md) added an MCP server as FinChat's agent
channel. It has run over stdio since: a subprocess on a developer's laptop, started by
whichever client is asking. That works, and it has one property that turns out to matter
more than any other — **it authenticates as the person running it**. `gcloud auth
print-identity-token` mints a token for the signed-in human, Cloud Run accepts it because
that human holds `run.invoker`, and [ADR-0019](0019-end-user-credential-propagation.md)
column-level security is evaluated against them. The masking a customer-service rep sees
is the masking they are entitled to.

The request that forced this decision: another GCP service — a separate AI gateway, in
its own repo, owned by a different concern — wants to call these tools. stdio cannot
cross that boundary. A Cloud Run container is not going to spawn a subprocess belonging
to another team, and it should not want to.

So the MCP server has to be reachable over HTTP. And the moment it is, the question stops
being about transport: **who is a remote caller, and what are they entitled to see?**

## Decision

**Deploy the MCP server as a private Cloud Run service. Authentication is Cloud Run IAM,
not MCP's own. Every caller is a named service account, and the server acts as itself.**

- `finchat-<env>-mcp`, scale-to-zero, `--no-allow-unauthenticated`, running as a
  dedicated `mcp` service account that holds **no project roles at all**. It reaches data
  the same way the web BFF does: `run.invoker` on the process API, the two system APIs and
  the agent, each granted per-target.
- Callers are declared in `var.mcp_client_service_accounts`, empty by default. An MCP
  endpoint nobody can invoke is the right thing to ship by accident.
- The consuming service is required to have **its own** service account. The first
  consumer ran as the project's default compute SA, which its *public* UI also used;
  granting "the gateway" would have granted the internet-facing service in the same
  breath. That is now a rule in the variable's description, not a thing to remember.

### The identity trade, stated rather than discovered

| | stdio (local) | HTTP (deployed) |
|---|---|---|
| Authenticates as | the person running the client | the MCP service account |
| CLS evaluated against | that person (ADR-0019) | the service |
| Reach | whatever that human holds | one fixed entitlement set |
| Revocation | remove the human's `run.invoker` | remove the caller's `run.invoker` |

The HTTP row is a real reduction in fidelity and it is the honest cost of this ADR. A
remote caller sees what *this service* is entitled to, not what the person behind the
request is. Two things make that acceptable rather than merely tolerated:

1. The MCP service holds no direct data access. It composes through the same governed
   APIs, so nothing here widens the perimeter — it only fixes the identity at one point
   instead of varying per user.
2. **The consumer is expected to enforce the person's own entitlements before calling.**
   The AI gateway resolves a verified email to a persona, checks that person's budget,
   and screens their prompt, all before a tool is reached. Per-user authorisation still
   happens; it happens one hop earlier.

What would close the gap is [ADR-0020](0020-remote-mcp-workspace-federation.md) — OAuth with dynamic
client registration, which is MCP's own answer and is still not built. Until it is,
"which human asked" is a claim the caller makes, not one this service verifies.

### What the consumer owes

A remote MCP client is not just a transport. The first consumer had to add four controls
that had no equivalent on its prompt-only surface, and they generalise:

- **Tool results are screened.** They come from a service the client does not own, enter
  the model's context, and leave on someone's screen.
- **The budget covers the loop.** A tool-calling answer is N model calls, not one.
- **The tool names are audited.** "Which model answered" is half the record; "what did it
  read" is the half a regulator asks about.
- **An unreachable tool server refuses.** It must not fall back to the model's own
  knowledge — a plausible balance for an account nobody looked at is worse than an error.
  This is the same rule as `GatewayRefused` in [ADR-0024](0024-enterprise-ai-gateway.md),
  arrived at independently, which is some evidence it is the right one.

### What this does *not* enable

Hosted clients — claude.ai connectors and anything else that cannot mint a Google OIDC
token — still cannot connect. They need ADR-0020. This ADR serves service-to-service
callers inside GCP and local developers; it does not open the endpoint to the internet,
and the allow-listed-hosts setting is not a substitute for that work.

## Amended 2026-09-07 — the endpoint is public in dev, and validates two caller kinds

This ADR chose Cloud Run IAM as the authentication, because MCP's own answer did not
exist yet. [ADR-0020](0020-remote-mcp-workspace-federation.md) has since been built, so
dev now serves `/mcp` publicly and validates tokens itself. The identity trade in the
table above is therefore no longer the whole story: a **person** using a hosted client
gets per-user identity back.

**Going public removes Cloud Run IAM, which was the only thing authenticating the in-GCP
callers.** The AI gateway and the BFF never went near the OAuth proxy — they present a
Google OIDC token and Cloud Run checked it. So the resource server now performs that same
check itself, and the two caller kinds are distinguished in the claims because "a person
asked" and "a service asked on nobody's behalf" are different facts about one request:

| Caller | Credential | Checked how |
|---|---|---|
| Person, hosted client | proxy-minted token | signature against the proxy JWKS, `alg` pinned, `aud` = this resource, `iss` = the proxy |
| Service, in GCP | Google OIDC token | Google's verifier with `aud` = the service URL, **plus** the email in `FINCHAT_MCP_SERVICE_CALLERS` |

The second row's last clause is the whole control. Verifying a Google signature and
stopping there accepts a token minted for this service by *any* Google identity.

**Two switches, and neither derives from the other.** `enable_mcp_oauth` provisions the
proxy; `mcp_public` removes Cloud Run IAM. Opening the door takes two deliberate changes
rather than one edit, and they were rolled out in that order on purpose — enforcement
proven while IAM was still in front, so a mistake in the new validation could not expose
anything. That staging paid for itself immediately: the deploy that enabled enforcement
**failed**, on a delimiter bug. Combined into one change it would have failed after the
public flag was set and before the server could validate anything.

**Signing keys are per environment.** One key across all three is a cross-environment
compromise path: audience and issuer checks stop a dev token being *replayed* at prod,
but not a dev key being used to *mint* one, because prod's JWKS would publish the same
public key.

**Both environments are public** (dev 2026-09-07, prod 2026-09-08), each rolled out in
the same order. There is no `test` any more — it was removed rather than given a third
OAuth callback, which would have exceeded the redirect-URI limit on an unverified Google
client.

Prod's `FINCHAT_MCP_SERVICE_CALLERS` names only its CI identity. dev carries three because
the AI gateway and the web BFF are wired there; nothing else calls prod's MCP, and copying
dev's list would have granted reach nobody uses.

**Signing keys and client stores are per environment.** One signing key across both is a
cross-environment compromise path: audience and issuer checks stop a dev token being
*replayed* at prod, but not a dev key being used to *mint* one, because prod's JWKS would
publish the same public key.

**The configuration that makes this safe is not in the repository.** `mcp_public` lives in
a gitignored `terraform.tfvars`, and CI materialises tfvars from a per-environment
`TFVARS` secret. Setting the variable locally and applying leaves that secret stale, and
the next CI apply silently reverts the environment — surfacing only as hosted clients
getting 403 with nothing tying it to a Terraform run. After editing any env tfvars,
re-sync that environment's `TFVARS` secret. The repo cannot tell you what prod's
configuration is.

## Consequences

- The knowledge-base tool finally reaches the deployed retrieval path. `FINCHAT_AGENT_URL`
  had never been set anywhere, so every `search_knowledge_base` call answered from the
  22-document local BM25 corpus and labelled itself `sparse-local`. The deploy sets it,
  which is a quality change arriving as a side effect of an infrastructure one.
- **API-management granularity collapses at the MCP boundary.** Every call is `POST /mcp`
  with a JSON-RPC body, so an API gateway sees one path, one quota bucket and one
  analytics row for thirteen tools. That is the sharp edge for anyone putting MCP behind
  Apigee: keying quota on `params.name` needs a policy that parses the body, and no
  product does it out of the box.
- Cold starts are now visible to a *model*, not a developer. Discovery is a connect plus
  `initialize`, so the consumer caches the tool catalogue; a server that gains a tool is
  visible within the TTL rather than after a redeploy.
- `FINCHAT_MCP_ALLOWED_HOSTS` must carry the service's own hostname. The SDK's
  DNS-rebinding protection trusts localhost only, so a proxied `Host` is refused with a
  bare `421` that says nothing about why. Same trap as any reverse-proxied MCP server.

## Alternatives considered

- **Leave it on stdio and have the consumer shell out.** Preserves per-user identity
  perfectly. Requires the consuming container to ship this repo's code and credentials,
  which makes every consumer a fork of FinChat.
- **Make the MCP service public and check a shared bearer token.** Simple, and the shape
  a lot of MCP servers ship with. Rejected: a static secret in an env var is one leak away
  from anonymous access to banking tools, and it produces no per-caller audit.
- **Wait for ADR-0020 and do OAuth properly first.** The correct end state. It is also
  weeks of work to serve one consumer that already has a Google identity, and it would
  have delayed the finding that a remote client needs four controls nobody had written.
- **Expose the tools as plain REST instead.** They already are — that is what the system
  and process APIs are, and MCP is a client of them. The value here is the *description*:
  schemas, refusal instructions and tool semantics a model can consume without a human
  writing an integration.
