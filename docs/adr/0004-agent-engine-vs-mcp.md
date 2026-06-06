# ADR-0004 — Vertex AI Agent Engine (with ADK) over a bare MCP server

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** Agentic AI, AgentOps, enterprise agent patterns

## Context

The platform needs an enterprise data-retrieval agent (Product 1) and a multi-agent loan workflow
(Product 2). Two hosting/orchestration philosophies were considered:

1. **Vertex AI Agent Engine + Google ADK** — a managed agent runtime with built-in session
   management, tracing, deployment, and an evaluation harness.
2. **A bare Model Context Protocol (MCP) server** — expose tools/data over MCP and let a generic
   client model call them.

These are often framed as competitors. They are not the same layer.

## Decision

Use **Google ADK to author agents** and **Vertex AI Agent Engine as the managed runtime** for
deployment, sessions, evaluation, and observability. **MCP is used *inside* the agent as a tool/
resource transport**, not as the orchestration layer.

## Rationale — why Agent Engine is "preferred vs MCP"

| Concern | Agent Engine + ADK | Bare MCP server |
|---|---|---|
| **Orchestration** | First-class workflow/sequential/parallel/loop agents, planner pattern | None — MCP is a tool protocol, not an orchestrator |
| **Session/state** | Managed sessions + Memory Bank for long-running context | You build state yourself |
| **Evaluation** | Built-in eval harness (grounding, tool-use, quality) → serves our AgentOps deliverable | Not provided |
| **Observability** | Native tracing, Cloud Logging/Monitoring integration | Roll your own |
| **Security** | Runs under a least-privilege SA, VPC-SC compatible, IAM-gated | Must be secured ad hoc |
| **Grounding/RAG** | Integrated retrieval + tool calling | Possible but unmanaged |
| **Deployment/versioning** | Managed deploy, traffic, rollback | Manual |

**MCP is complementary, not a substitute.** MCP standardizes *how an agent reaches a tool or data
source*; Agent Engine governs *how the agent runs, remembers, is evaluated, and is operated* in
production. For a regulated bank, the operational/governance envelope (sessions, eval, tracing, IAM)
is exactly what differentiates a demo from a production agent — and that is what Agent Engine adds on
top of whatever transport (including MCP) the tools use.

## Consequences

- Agents are authored once in ADK and deployable to **either** Agent Engine **or** Cloud Run (fallback),
  preserving portability (see [ADR-0007](0007-cloud-run-vs-gke.md)).
- We get the evaluation harness "for free," directly feeding [eval/](../../eval/).
- Cost is controlled by **scale-to-zero** (accept cold start) per the
  [cost estimate](../08-cost-estimate.md); a warm pool is the enterprise toggle.
- Where tools benefit from a standard interface, they may be exposed via **MCP** and registered as
  ADK tools — getting both standardization and managed operations.
