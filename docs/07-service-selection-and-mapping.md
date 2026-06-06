# 07 — Service Selection & Sandbox→Enterprise Mapping

> **Purpose.** Justify every GCP service choice, and where the sandbox deploys a cheaper substitute,
> document the **1:1 enterprise mapping** so the architecture remains valid at Fortune 500 scale.
> This is the document that lets a reviewer trust that "near-zero cost" did not mean "not enterprise."

## Decision principle

For each capability we ask three questions:

1. **Does the preferred service scale to zero?** If yes → deploy it as-is.
2. **If not, is its enterprise capability *needed* in the sandbox to prove the architecture?** If no → substitute with a serverless equivalent and document the mapping.
3. **Does the substitute preserve the same logical contract** (same API shape, same data flow, same security boundary) so migration is configuration, not redesign?

A substitution is only acceptable when the migration to the enterprise service is **a deployment change, not an architecture change.**

---

## Capability-by-capability

### Storage & Analytics — BigQuery + BigLake → *deployed as-is*
- **Why:** BigQuery is serverless, separates storage from compute, on-demand pricing includes 1 TiB/mo free query + 10 GiB/mo free storage. BigLake adds fine-grained ACLs over Cloud Storage and a unified governance surface.
- **Scale path:** on-demand → reservations/editions (Standard/Enterprise/Enterprise Plus) with slot autoscaling. No schema or query change.
- **Sandbox cost:** ~$0 at demo volume.

### Eventing — Pub/Sub → *deployed as-is*
- **Why:** Serverless, scale-to-zero, 10 GiB/mo free. The decoupling backbone for event-driven architecture. Native **BigQuery subscription** and **dead-letter topic** support.
- **Scale path:** identical; Pub/Sub handles millions of msgs/sec natively.

### Stream Processing — Dataflow (Apache Beam) → *deployed on-demand* ⚠️
- **Sandbox:** Beam pipeline packaged as a **Flex Template**, launched per generation run, **drains on completion** so no workers idle. This preserves the real Beam/Dataflow code path while keeping idle cost ~$0.
- **Enterprise target:** the **same template** run as a **24/7 streaming job** with autoscaling + Streaming Engine.
- **Mapping:** identical pipeline code; only the launch mode (`--streaming` job lifetime) changes. See [ADR-0003](adr/0003-dataflow-on-demand-streaming.md).
- **Why not Pub/Sub→BQ direct subscription?** We *also* support that as the cheapest path, but Dataflow earns its place by demonstrating in-flight DLP, validation, enrichment, and DLQ routing — the transforms a bank actually needs.

### API Management — **Cloud API Gateway / Endpoints** → substitutes **Apigee X** 🔁
- **Substitute:** API Gateway fronts Cloud Run services; OpenAPI-driven routing, API keys, quotas, JWT auth. First 2M calls/mo free, scales to zero.
- **Enterprise target:** **Apigee X** for monetization, advanced mediation/transformation policies, developer portal, hybrid/multi-cloud gateways, deep analytics.
- **Why substituted:** Apigee has **no scale-to-zero and no permanent free tier** (~$350–700+/mo PAYG idle; $20k–100k+/yr subscription). None of those premium capabilities are needed to prove DaaS.
- **Mapping:** the **same OpenAPI specs** import directly into Apigee as API proxies. Migration = re-host the contract. See [ADR-0006](adr/0006-api-gateway-vs-apigee.md).

### Orchestration — **Cloud Workflows + Cloud Scheduler** → substitutes **Cloud Composer** 🔁
- **Substitute:** Cloud Workflows for the long-running loan process (native HTTP callbacks → ideal for human-in-the-loop waits), Cloud Scheduler for any cron triggers. Both serverless, ~$0 idle.
- **Enterprise target:** **Cloud Composer (Airflow)** *for batch DAG orchestration* — nightly reconciliation, regulatory batch reporting, dbt runs, ML training schedules.
- **Why substituted:** Composer is always-on GKE (~$300–500/mo) and is **the wrong tool for event-driven streaming + long HITL waits**. It is genuinely not needed here. See [ADR-0005](adr/0005-workflows-vs-composer.md).
- **Mapping:** the loan workflow stays on Workflows even at enterprise scale; Composer is *added* later for batch, not a replacement.

### Agent Runtime — **Vertex AI Agent Engine (scale-to-zero)** → *deployed as-is, idle-tuned*
- **Why:** Managed runtime for ADK agents with managed sessions, tracing, and an evaluation harness — directly serves the AgentOps deliverable. Runtime billed on vCPU-hr + GiB-hr; allow scale-to-zero → ~$0 idle (accept cold start). Gemini tokens billed per use.
- **Enterprise target:** same service with a **warm minimum-instance pool** for latency SLOs.
- **Fallback substitute:** the identical ADK agent also runs on **Cloud Run** (clean scale-to-zero) if Agent Engine quota/cost is a concern. See [ADR-0004](adr/0004-agent-engine-vs-mcp.md).

### Application Hosting — Cloud Run → *deployed as-is*
- **Why:** Scale-to-zero containers, per-100ms billing, 2M requests/mo free. Hosts DaaS APIs, loan API, agents (fallback), and the UI. See [ADR-0007](adr/0007-cloud-run-vs-gke.md) for Run vs GKE.

### Governance — Cloud DLP + Data Catalog (Dataplex) → *deployed as-is (scoped)*
- **DLP:** inspection + de-identification templates; invoked in-pipeline (sampled in sandbox to control cost).
- **Data Catalog / Dataplex:** tag templates for data classification (PII, Confidential, Public), lineage capture.
- **Scale path:** scheduled DLP profiling jobs + org-wide Dataplex lakes.

### Supporting — Cloud Storage, Secret Manager, Artifact Registry, Cloud Build, Cloud Logging/Monitoring → *deployed as-is*
- All serverless / pay-per-use with free tiers; standard platform plumbing.

---

## Summary table

| Capability | Preferred (enterprise) | Sandbox (deployed) | Idle cost saved | Migration effort |
|---|---|---|---|---|
| Analytics | BigQuery/BigLake | same | — | n/a |
| Eventing | Pub/Sub | same | — | n/a |
| Stream processing | Dataflow 24/7 | Dataflow on-demand | ~$200+/mo | launch flag |
| API management | **Apigee X** | **API Gateway** | ~$350–700/mo | import OpenAPI |
| Orchestration | **Composer** | **Workflows + Scheduler** | ~$300–500/mo | add Composer for batch |
| Agent runtime | Agent Engine (warm) | Agent Engine (scale-0) / Cloud Run | ~$80–130/mo | min-instances |
| App hosting | Cloud Run | same | — | n/a |
| Governance | DLP + Dataplex | same (scoped) | — | unscope/schedule |

**Total avoided idle spend: ~$930–1,530+/month** with zero loss of architectural fidelity.
