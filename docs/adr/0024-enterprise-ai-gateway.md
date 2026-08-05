# ADR-0024 — Enterprise AI Gateway as the enforcement point

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Principal Data Architect
- **Context tags:** AI platform, governance, FinOps, PII, enforcement

## Context

FinChat had six LLM call sites, each calling Vertex directly with its own ad-hoc posture:

| Call site | Screening | Budget | Attribution |
|---|:-:|:-:|:-:|
| Banking Assistant (ADK) | Model Armor at the BFF | — | — |
| Loan agents (ADK) | — | — | — |
| Analyst intent router | — | — | — |
| Semantics answerer | — | — | — |
| LLM-judge (live eval) | — | — | — |
| Steward generator | — | — | — |

One call site had screening, none had a budget, none had per-use-case attribution. That is
the "every project builds its own partial gateway" pattern the platform Solution Approach
exists to end, reproduced inside a single repository.

Cloud billing cannot close this. Vertex `generateContent` accepts a `labels` field that
flows to Cloud Billing and BigQuery export — good for **showback**, useless for
**enforcement**, because billing data is not real-time. Enforcement has to happen in the
request path or not at all.

## Decision

Route model calls through the **Bank AI Gateway** as the single policy enforcement point,
via a new service-to-service endpoint.

### `/v1/complete` — a machine surface, separate from `/v1/chat`

The gateway's existing `/v1/chat` is shaped for humans: persona resolution by email,
conversation history, long-term memories, context compaction. Agents need none of that,
and giving them conversational memory would be a data-retention decision nobody made.

`/v1/complete` runs the same control pipeline — budget → PII screen → tier routing → model
call → response screen → audit — with attribution to an **`agent_id` and `workload_class`**
instead of a human persona, and no conversation state.

### `/v1/generate` — a structured surface, because agents are not prompts

`/v1/complete` takes a prompt string. An agent turn is not one: it carries function
declarations, and often a `functionCall` the model emitted plus the `functionResponse` the
runtime returned. Flattening it would silently strip the agent's tools — and would look
like it worked right up until an agent stopped calling them.

So agents use `/v1/generate`, which applies the same controls *around* a full
`generateContent` body and forwards it intact. Two consequences follow:

- **PII screening runs over text parts and skips `functionResponse` payloads.** Those are
  governed tool output the platform produced; screening them would flag exactly the account
  data the agent was asked to fetch.
- **A response-side finding is reported, not redacted.** Blanking a part mid-conversation
  would corrupt a function-call turn and present as a model bug. Blocking belongs on the
  request side.

FinChat consumes this through an ADK `BaseLlm` implementation (`gateway_llm.py`), so agent
code, instructions and tools are untouched — only the `model=` argument changes, and each
agent attributes under its own registry id.

### Workload classes carry the budget and clamp the tier

`workloads.py` registers five classes (`tool_calling_agent`, `grounded_generation`,
`reasoning`, `evaluation`, `classification`), each with a daily allowance and an optional
tier clamp. Two decisions inside that are deliberate:

- **Budget is charged per agent, not per class.** Charging per class would let one noisy
  agent exhaust the allowance of every other agent doing similar work, which makes the
  control unusable and the attribution meaningless. The class supplies the limit; the
  agent owns the spend.
- **`classification` and `evaluation` are clamped to the standard tier.** Routing a
  one-word intent classification to a premium model is the most common way agent platforms
  waste money, and it is prevented at the gateway rather than trusted to each caller.

An unregistered workload class is **rejected**, matching the gateway's posture on
unprovisioned humans. Defaulting unknown callers to a permissive tier is how a gateway
degrades into a proxy.

### Fallback is direct-to-Vertex, and it is counted

If the gateway is unconfigured or unreachable, the call still succeeds against Vertex — a
governance layer that takes the product down when it hiccups gets removed within a quarter.
But every such call increments a bypass counter, and `/api/gateway/transit` reports the
share. **A bypass you don't count is a bypass you will report as compliance.**

### A policy refusal is not a fallback

`GatewayBlocked` is a distinct exception from a transport failure. PII block, budget
exhaustion, and unregistered workload propagate; they are never retried directly against
Vertex. Collapsing refusal and unavailability into one "gateway didn't work, go direct"
path would let the control fire and be routed around in the same request — the one failure
mode a gateway must not have.

## Consequences

**Positive**

- Screening, budgets, tiering, and audit are consumed rather than reimplemented per call site.
- Token spend is attributable to an agent and a workload class, which is the input the
  unit-economics rollup joins to eval outcomes ([docs/22](../22-ai-unit-economics.md)).
- Tier clamping makes model tiering a platform behaviour rather than caller discipline.
- Transit share is measurable, including its structural gaps.

**Negative / accepted**

- **One of six call sites cannot transit.** The managed analyst Data Agent has no
  injectable model endpoint, so it is governed by perimeter and credential controls
  instead. Recorded as a structural bypass rather than omitted from the denominator.
  Honest transit share is **5 of 6**, not 100%.
- **The ADK adapter duplicates a module across two build contexts.** `gateway_llm.py`
  exists in both `products/transactions/agent` and `products/loans/agents` because they
  are separate Docker build contexts and neither can import a shared path. A CI test
  asserts the copies are byte-identical; that is a guard, not a fix.
- **An extra network hop per call.** Small against model latency, not zero.
- **The gateway becomes a tier-1 dependency** for the paths that use it — mitigated by the
  counted fallback, which is itself a deliberate weakening of the control.
- **Budgets are advisory until proven.** Enforcement is a known weak point across gateway
  products: several enforce retroactively by one request, several fail to aggregate across
  instances, and token accounting commonly degrades on streaming responses. FinChat's
  paths are non-streaming, so this is not yet exercised — but the limitation is real and
  must be validated under a streaming load profile before budgets are relied on.
- **Cross-process budget state depends on Firestore.** The gateway degrades to in-memory
  counting when Firestore is unavailable, which means a budget can be over-spent during an
  outage. Degrading open was the right call for availability; it is still a gap.

## Alternatives considered

- **Apigee as the enforcement point.** The enterprise target, and the right answer at
  Huntington scale given existing investment. Not used here because Apigee has no
  scale-to-zero tier (~$350–700/mo idle), which breaks FinChat's near-zero-cost premise —
  the same substitution logic as [ADR-0006](0006-api-gateway-vs-apigee.md), documented 1:1
  rather than pretended away.
- **Google's managed Agent Gateway.** Rejected on perimeter grounds: it does not support
  VPC Service Controls, and neither do semantic governance policies, while Agent Runtime,
  Sessions and Memory Bank do. Adopting it would mean leaving the perimeter precisely at
  the component providing tool allow-listing.
- **Vertex request labels for attribution.** Adopted as complementary showback, rejected as
  enforcement — billing export is not real-time and cannot refuse a request.
- **Per-call-site guardrails.** The status quo. Rejected: it is the problem.

## References

- [23 — Gateway transit & bypass](../23-gateway-transit.md)
- [22 — AI unit economics](../22-ai-unit-economics.md)
- [ADR-0008 — Model Armor screening](0008-model-armor-llm-screening.md)
- [ADR-0022 — Model version pinning](0022-model-version-pinning.md)
- [ADR-0023 — Agent registry and identity](0023-agent-registry-and-identity.md)
