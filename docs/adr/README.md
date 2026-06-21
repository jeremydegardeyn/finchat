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
| [0008](0008-model-armor-llm-screening.md) | Model Armor for runtime LLM I/O screening | Accepted |
| [0009](0009-bigquery-vector-rag.md) | BigQuery-vector RAG for the conversational agent | Accepted |
| [0010](0010-agents-on-cloud-run.md) | Agents on Cloud Run (scale-to-zero) over Agent Engine | Accepted |
| [0011](0011-dataplex-universal-catalog.md) | Dataplex Universal Catalog as discovery/metadata/AI-context layer | Accepted |
| [0012](0012-conversational-analytics.md) | Analyst persona: catalog discovery + Conversational Analytics API | Accepted |
| [0013](0013-loan-decision-explainability.md) | Loan decision explainability via per-factor attribution + reason codes | Accepted |
| [0014](0014-knowledge-graph-semantic-layer.md) | Knowledge Graph as the semantic grounding layer for conversational AI | Accepted |
| [0015](0015-live-evaluation.md) | Live evaluation — LLM-judge scoring of real production conversations | Accepted |
| [0016](0016-identity-resolved-personas.md) | Identity-resolved personas (Google Sign-In + BFF-enforced RBAC) | Accepted |
| [0017](0017-bigtable-hot-path.md) | Bigtable hot-path serving tier for operational reads (default-off) | Accepted |
| [0018](0018-analyst-semantic-perimeter.md) | Analyst semantic perimeter + persistent Gemini Data Agent | Accepted |
| [0019](0019-end-user-credential-propagation.md) | End-user OAuth credential propagation to the analytics path | Accepted |
| [0020](0020-remote-mcp-workspace-federation.md) | Remote MCP access federated to Workspace / Cloud Identity via OAuth proxy | Accepted |
| [0021](0021-durable-agent-harness.md) | Durable-execution harness for long-running agents (DBOS deploy, Temporal documented 1:1) | Accepted |

_Future ADRs (planned): row/column-level security model, idempotency & exactly-once strategy,
environment promotion gating._
