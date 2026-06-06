# Architecture Decision Records

Lightweight ADRs capturing significant, hard-to-reverse decisions and their rationale.

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](0001-medallion-architecture.md) | Medallion (Bronze/Silver/Gold) on BigQuery + BigLake | Accepted |
| [0002](0002-serverless-substitution-strategy.md) | Serverless scale-to-zero substitution + enterprise mapping | Accepted |
| [0003](0003-dataflow-on-demand-streaming.md) | Dataflow on-demand (Flex Template) vs 24/7 streaming | Accepted |
| [0004](0004-agent-engine-vs-mcp.md) | Vertex AI Agent Engine + ADK over bare MCP | Accepted |
| [0005](0005-workflows-vs-composer.md) | Cloud Workflows + Scheduler over Cloud Composer | Accepted |
| [0006](0006-api-gateway-vs-apigee.md) | Cloud API Gateway over Apigee X (sandbox) | Accepted |
| [0007](0007-cloud-run-vs-gke.md) | Cloud Run over GKE for service hosting | Accepted |

_Future ADRs (planned): row/column-level security model, idempotency & exactly-once strategy,
multi-agent state management, environment promotion gating._
