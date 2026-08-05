# 23 — Gateway Transit & Bypass

> What share of FinChat's model calls actually go through the enforcement point, and what
> doesn't — stated as a number with its gaps, not as a claim.
>
> Decision and rationale: [ADR-0024](adr/0024-enterprise-ai-gateway.md).

## Why this document exists at all

"Share of AI workloads transiting the gateway" is the headline measure of a gateway
programme, and it is the easiest one to fake. Every bypass that isn't counted gets reported
as compliance. So the denominator here includes call sites the gateway *cannot* currently
serve, with the reason stated, rather than a flattering ratio over the subset that happens
to work.

## Current state

| # | Call site | Workload class | Transits | Why not |
|---|---|---|:-:|---|
| 1 | Analyst intent router (BFF) | `classification` | ✅ | — |
| 2 | Semantics answerer (BFF) | `grounded_generation` | ✅ | — |
| 3 | Steward generator | `reasoning` | ✅ | — |
| 4 | Banking Assistant | `tool_calling_agent` | ✅ | via the ADK `BaseLlm` adapter |
| 5 | Loan agents (×5) | `tool_calling_agent` | ✅ | Same — each under its own registry id |
| 6 | Analyst Data Agent | — | ❌ | Managed Conversational Analytics; no injectable model endpoint exists |

**5 of 6 call sites.** The remaining gap is structural: there is no seam in a managed
service to route through, so it will not close by finishing wiring.

This now includes the customer-facing traffic, which is the volume that matters — the
earlier 3/6 state governed only the cheap analytical paths.

Live, per-process:

```
GET /api/gateway/transit        # admin-gated
```

Counters reset on cold start (Cloud Run scales to zero), so treat them as a spot check.
The durable record is the gateway's own audit table.

## Why agents needed a second surface

`/v1/complete` takes a prompt string. An agent turn is not a string — it carries function
declarations, and often a `functionCall` the model emitted plus the `functionResponse` the
runtime returned. Flattening that would silently strip the agent's tools, and it would look
like it worked until an agent stopped calling them.

So agents use **`/v1/generate`**, which governs the request and forwards it *structurally*:
same controls, applied around the payload rather than by rewriting it. Two consequences
worth knowing:

- **PII screening runs over text parts and skips `functionResponse` payloads.** Those are
  governed tool output the platform itself produced; screening them would flag exactly the
  account data the agent was asked to fetch. The response screen catches the real risk.
- **A response-side PII finding is reported, not redacted.** Blanking a part mid-conversation
  would corrupt a function-call turn and surface as a model bug. Blocking belongs on the
  request side.

## What the gateway enforces on the paths that do transit

| Control | Behaviour |
|---|---|
| Workload registration | Unregistered class → rejected, not defaulted |
| Daily token budget | Charged **per agent**, limit supplied by the workload class |
| PII screen (prompt) | Model Armor + local detectors; block or redact per config |
| Tier routing | `classification` and `evaluation` clamped to the standard tier |
| PII screen (response) | Reply screened before it reaches the caller |
| Audit | Agent, owner, workload class, tier, tokens, PII verdict, serving version |

## The fallback, and its one hard rule

If the gateway is unconfigured or unreachable, the call proceeds directly to Vertex and is
counted as a bypass. A governance layer that takes the product down when it hiccups gets
removed within a quarter, so this weakening is deliberate.

**But a policy refusal is never a fallback.** PII block, budget exhaustion, and
unregistered workload raise `GatewayBlocked` and propagate. Retrying a refusal directly
against Vertex would route around the control in the same request that fired it — the one
failure mode a gateway must not have, and the thing
[`test_gateway_client.py`](../ui/test_gateway_client.py) exists to pin down.

## Closing the gap

1. ~~**ADK `BaseLlm` adapter**~~ — **done.** `gateway_llm.py` implements ADK's `BaseLlm`
   over the gateway's structured `/v1/generate` surface, so agent code, instructions and
   tools are untouched; only the `model=` argument changed. Each of the six agents
   attributes under its own registry id.
2. **Managed Data Agent** — cannot be routed. The compensating controls are the semantic
   perimeter ([ADR-0018](adr/0018-analyst-semantic-perimeter.md)) and end-user credential
   propagation ([ADR-0019](adr/0019-end-user-credential-propagation.md)), which constrain
   what it can reach rather than what it costs. Worth stating that this is a *different*
   control, not an equivalent one.
3. **Org policy on direct model egress** — the enterprise answer. Once transit is high
   enough, an organization policy constraint makes direct Vertex access unavailable, so
   bypass stops being a measurement and becomes an impossibility. Now plausible at 5/6 —
   the blocker is that the managed Data Agent still needs direct access.

## Known limits

- **Budget enforcement is unproven under streaming.** Token accounting degrades on
  streaming responses across essentially every gateway product, and streaming is how most
  production chat traffic flows. FinChat's governed paths are non-streaming today, so this
  is untested rather than solved.
- **Budget state degrades open.** When Firestore is unavailable the gateway counts
  in-memory, so a budget can be overspent during an outage.
- **Per-process counters are not a compliance metric.** They answer "is this working right
  now", not "what was our transit share last quarter" — that question needs the audit table.
