# ADR-0028 — MCP is the agent channel's experience API, not a second data path

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Principal Cloud Architect
- **Context tags:** MCP, API-led connectivity, experience APIs, agent authorization, least privilege, governance

## Context

[ADR-0004](0004-agent-engine-vs-mcp.md) settled that MCP is a **transport**, not an
orchestrator, and declined to build the platform *on* it.
[ADR-0020](0020-remote-mcp-workspace-federation.md) then designed how a remote MCP
client would authenticate — an OAuth proxy federating to Cloud Identity — but nothing
was built, because there was no MCP surface for it to front.

What has changed is the demand. External assistants and other teams' agents now want
to reach FinChat's capabilities directly, and the request arrives as "expose the APIs
over MCP." Taken literally that is a generator problem: read the OpenAPI, emit a tool
per operation, ship it. Taken literally it is also how a governed platform acquires an
ungoverned side door, because the thing that makes FinChat's APIs safe is not their
shape — it is the perimeter, the refusal policy, the per-user credential propagation
and the audit trail wrapped around them.

So the question this ADR answers is not "should we have an MCP server" but **what
layer the MCP server sits at, and what it is allowed to be a shortcut for.**

## Decision

**Build one MCP server that is a client of the existing governed APIs, and treat it as
an experience API for the agent channel** — a peer of the web BFF, not a peer of the
DaaS API.

```
Experience   │  Web BFF (ui/server.py)      MCP server (mcp_server/)      ← channel-shaped
─────────────┼──────────────────────────────────────────────────────────
Process      │  loan workflow · analyst router · risk scoring            ← orchestration
─────────────┼──────────────────────────────────────────────────────────
System       │  txn DaaS API · loan API                                  ← domain-shaped
             │  BigQuery Gold · CLS · Dataplex
```

Four commitments follow from that placement, and they are the decision:

1. **No tool reaches a data store directly.** Every tool calls the same private Cloud
   Run APIs the UI calls. A tool that queried BigQuery would inherit none of the query
   caps, the Gold-only serving rule or the audit trail, and — the part that matters —
   it would not appear in the gateway-transit denominator that is supposed to catch
   exactly this ([docs/23](../23-gateway-transit.md)).

2. **The knowledge plane is published alongside the tools, from the same SSOT.**
   Perimeter, join paths, glossary, refusal rules, stewardship, contracts and the
   ontology are MCP *resources*, compiled by `scripts/compile_okf.py` from
   `knowledge/ontology.yaml`. A remote client and our own analyst agent are grounded on
   one artifact. Publishing tools without this is how "revenue" becomes something
   nobody certified.

3. **The refusal policy travels as the server's instructions.** MCP hands a server's
   instructions to the client model. That is the only place in the protocol where a
   *server* gets to constrain a *client's* behaviour, so the platform's stated refusal
   rules go there — verbatim, generated, and pinned by a test.

4. **Omissions are decisions and get tests.** The human-in-the-loop loan decision
   (`POST /v1/loans/{id}/decision`) is exposed to no persona. Writes are off unless
   `FINCHAT_MCP_ALLOW_WRITES` is set. Both are asserted negatively, because an omission
   with no test becomes an oversight the first time someone adds a tool by copying the
   one above it.

### Identity, by deployment shape

| Caller | Transport | Principal | Mechanism |
|---|---|---|---|
| Desktop client on a laptop | stdio | the signed-in human | gcloud ID token → private Cloud Run; BigQuery CLS evaluated against them (ADR-0019) |
| Another GCP service / agent | streamable-HTTP | that service's SA | Cloud Run IAM, `roles/run.invoker`, OIDC ID token |
| Hosted assistant, partner agent | streamable-HTTP | the human, via their org | OAuth 2.1 + DCR proxy federating to Cloud Identity ([ADR-0020](0020-remote-mcp-workspace-federation.md)) |

The first two need no new infrastructure and no new authorization server. Only the
third does, which is why it stays a separate, later decision rather than a prerequisite.

**`FINCHAT_MCP_PERSONA` scopes which tools are offered. It is not access control** — it
is a client-side environment variable, and calling it a control would be the exact
self-deception docs/23 exists to avoid. Enforcement stays where the caller cannot edit
it: IAM on the private services, CLS in BigQuery, the approver check inside the loan API.

## Consequences

- The MCP surface inherits every control the APIs already have, and adds no new path to
  audit. The transit measurement stays meaningful.
- Commitment 1 forced one addition rather than one shortcut: the knowledge base lives in
  BigQuery behind the agent, so exposing it meant giving the agent service a retrieval-only
  `POST /search` and calling that — not querying BigQuery from a tool, and not wrapping
  `/chat` and returning another model's prose. Retrieval stays one implementation.
- Adding a capability means adding it to a **system or process API first**, then
  exposing it. That is friction, and it is the intended friction: it is what stops the
  agent channel from becoming the place undocumented capabilities accumulate.
- The server reuses four modules and one data file owned by other services (the compiled
  OKF context, two demo repositories, the KB's BM25 ranker, the KB corpus). They are
  reached by path and asserted by an image guard, because they are read lazily — a missing
  file survives the build, boot and health check, then surfaces as one broken tool in
  production.
- Demo mode means `claude mcp add` works before any GCP access exists. It also means a
  client can be talking to synthetic data without knowing, so `finchat_status` reports
  the data source and says so.
- **Not decided here:** deploying the HTTP transport, and the OAuth proxy. The image and
  the transport exist; standing them up is a cost decision that has not been taken.

## Alternatives considered

- **Generate tools from the OpenAPI spec.** Fast, and the obvious hackathon move. It
  produces a tool per operation with no perimeter, no refusal policy and no semantics —
  a model that can call `getBalance` but does not know that PENDING rows do not count.
  The generator is the right *starting point* and the wrong *finished product*; see
  [docs/27](../27-mcp-service.md) for the middle path.
- **MCP directly over BigQuery.** Shortest path to an impressive demo, and it deletes
  the entire control plane. Declined for the reason in commitment 1.
- **One MCP server per data product.** More mesh-shaped, and defensible later. Declined
  now because the knowledge plane is shared and the perimeter is a platform-level
  concept; two servers means two copies of it, and the drift starts immediately.
- **Expose the loan decision as a tool with a confirmation prompt.** A client-side
  confirmation is a client-side control. The workflow's human approval step exists
  precisely so that the decision is not a model's to make.
