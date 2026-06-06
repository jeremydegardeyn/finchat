# ADR-0002 — Serverless scale-to-zero substitution strategy (with documented enterprise mapping)

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** Cost engineering, platform engineering

## Context

Several "preferred" enterprise services bill while idle and have no free tier (Apigee, Composer) or
incur continuous cost (Dataflow 24/7, warm Agent Engine). The sandbox must be near-zero cost **without
compromising architectural credibility** for a Fortune 500 review.

## Decision

Establish a platform-wide rule: **deploy the serverless scale-to-zero equivalent, and document a 1:1
enterprise mapping** for every substituted service, such that migration is **configuration, not
re-architecture**. A substitution is acceptable only if it preserves the same logical contract (API
shape, data flow, security boundary).

## Substitutions

| Capability | Sandbox | Enterprise | ADR |
|---|---|---|---|
| API management | API Gateway/Endpoints | Apigee X | [0006](0006-api-gateway-vs-apigee.md) |
| Orchestration | Workflows + Scheduler | Composer | [0005](0005-workflows-vs-composer.md) |
| Stream processing | Dataflow on-demand | Dataflow 24/7 | [0003](0003-dataflow-on-demand-streaming.md) |
| Agent runtime | Agent Engine scale-0 / Cloud Run | Agent Engine warm | [0004](0004-agent-engine-vs-mcp.md) |

## Consequences

- Saves ~$930–1,530+/mo idle (see [cost estimate](../08-cost-estimate.md)).
- Each substitution is justified in its own ADR and the [service mapping](../07-service-selection-and-mapping.md).
- Terraform exposes the enterprise toggles (min-instances, job lifetime) as variables so promotion is
  a tfvars change.
