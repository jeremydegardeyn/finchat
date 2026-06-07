# FinChat — Enterprise Banking Data & AI Platform (GCP Reference Implementation)

> A production-grade, enterprise-scale **Data & AI platform** for retail banking, implemented as a
> near-zero-cost reference architecture on Google Cloud. Designed to be reviewed by a Fortune 500
> banking architecture board, and engineered to scale to **billions of transactions / millions of
> customers** without architectural redesign.

**Project:** `strongsville-city-schools` · **Region:** `us-central1` · **Host:** `finchat.datadinosaur.com`

---

## 1. Executive Architecture Overview

FinChat demonstrates a modern **data mesh** on Google Cloud composed of two independent, interoperable
**data products**, fronted by **agentic AI** and **Data-as-a-Service** APIs:

| # | Data Product | Pattern | Core capability |
|---|--------------|---------|-----------------|
| **1** | **Banking Transactions** | Real-time event-driven ledger | Streaming ingestion → Medallion (Bronze/Silver/Gold) → DaaS APIs → Conversational data agent |
| **2** | **Loan Approval** | Long-running agentic workflow | Multi-agent orchestration with human-in-the-loop approval, auditability, AI decision support |

The platform is built on **eleven architectural pillars**, each mapped to concrete GCP services and
documented in the [`/docs`](docs/) deliverables:

1. **Data as a Product** — each domain owns its schema, SLAs, pipeline, and access contract.
2. **Data as a Service** — REST APIs (OpenAPI-first) expose curated Gold data with governance.
3. **Event-Driven Architecture** — Pub/Sub backbone; producers and consumers decoupled.
4. **Real-Time Processing** — Dataflow (Apache Beam) streaming, run on-demand for cost control.
5. **Agentic AI** — Google ADK + Gemini on Vertex AI Agent Engine; tool-calling + RAG grounding.
6. **AgentOps / MLOps** — versioned agents, evaluation datasets, grounding/hallucination metrics.
7. **API-First Design** — every capability has an OpenAPI contract before implementation.
8. **Data Governance** — DLP/PII masking, column- & row-level security, Data Catalog, lineage.
9. **Enterprise Security** — least-privilege IAM, per-service service accounts, CMEK-ready, audit.
10. **Platform Engineering** — Terraform modules, CI/CD, dev→test→prod promotion.
11. **Cost Engineering** — serverless scale-to-zero by default; premium services mapped, not deployed.

### Cost philosophy: deploy cheap, document enterprise

This sandbox is optimized for **near-zero idle cost**. Where a "preferred" enterprise service bills
while idle (Apigee, Cloud Composer), we deploy a **serverless scale-to-zero substitute** and document
the **1:1 enterprise mapping** so the design remains credible at F500 scale. See
[`docs/07-service-selection-and-mapping.md`](docs/07-service-selection-and-mapping.md).

| Capability | Sandbox (deployed, ~$0 idle) | Enterprise target | Why substituted |
|---|---|---|---|
| API management | **Cloud API Gateway / Endpoints** | **Apigee X** | Apigee has no scale-to-zero / free tier (~$350–700+/mo idle) |
| Batch orchestration | **Cloud Workflows + Scheduler** | **Cloud Composer (Airflow)** | Composer is always-on GKE (~$300–500/mo); not needed for streaming + HITL |
| Stream processing | **Dataflow (on-demand Flex Template)** | **Dataflow (24/7 streaming)** | Run-per-generation drains workers → near-$0 idle |
| Agent runtime | **Agent Engine (scale-to-zero)** / Cloud Run | **Agent Engine (warm pool)** | Idle ~$0; accept cold start in sandbox |

> **Net sandbox running cost: only Dataflow while a generation run is active.** Everything else scales
> to zero. Full breakdown in [`docs/08-cost-estimate.md`](docs/08-cost-estimate.md).

---

## 2. Repository Structure

```
finchat/
├── README.md                      # This file — executive overview + navigation
├── docs/                          # All architecture deliverables (the "architect's binder")
│   ├── 00-executive-overview.md
│   ├── 01-logical-architecture.md
│   ├── 02-physical-architecture.md
│   ├── 03-data-flow.md
│   ├── 04-agent-architecture.md
│   ├── 05-api-architecture.md
│   ├── 06-security-architecture.md
│   ├── 07-service-selection-and-mapping.md   # sandbox → enterprise 1:1 mapping
│   ├── 08-cost-estimate.md
│   ├── 09-data-governance.md
│   ├── 10-deployment-runbook.md
│   ├── 11-future-state-roadmap.md
│   ├── data-model.md                          # schemas, keys, partitioning, classification
│   ├── diagrams/                              # Mermaid sources
│   └── adr/                                   # Architecture Decision Records
├── infra/                         # Terraform (Infrastructure as Code)
│   ├── modules/                   # reusable modules (bigquery, pubsub, dataflow, ...)
│   └── envs/{dev,test,prod}/      # per-environment composition + backends
├── products/
│   ├── transactions/              # Data Product 1
│   │   ├── generator/             # synthetic transaction generator (≤10k/run, ≤4/customer)
│   │   ├── pipeline/              # Apache Beam / Dataflow streaming pipeline
│   │   ├── schemas/               # BigQuery DDL + JSON schemas (Bronze/Silver/Gold)
│   │   ├── api/                   # Cloud Run DaaS API (balance, history, summary)
│   │   └── agent/                 # ADK conversational data agent
│   └── loans/                     # Data Product 2
│       ├── workflow/              # Cloud Workflows orchestration (long-running + HITL)
│       ├── api/                   # loan submission + approver decision API
│       └── agents/                # Planner / Credit / TxnReview / Approval / Notification
├── ui/                            # lightweight web UI (Customer / Employee / Admin personas)
├── eval/                          # agent evaluation framework (datasets, pipelines, reports)
├── cicd/                          # GitHub Actions + Cloud Build + promotion strategy
└── scripts/                       # bootstrap, deploy helpers, smoke tests
```

---

## 3. Architecture Deliverables Index

| # | Deliverable | Location |
|---|-------------|----------|
| 1 | Executive Architecture Overview | this README + [`docs/00`](docs/00-executive-overview.md) |
| 2 | Logical Architecture Diagram | [`docs/01`](docs/01-logical-architecture.md) |
| 3 | Physical Architecture Diagram | [`docs/02`](docs/02-physical-architecture.md) |
| 4 | Data Flow Diagram | [`docs/03`](docs/03-data-flow.md) |
| 5 | Agent Architecture Diagram | [`docs/04`](docs/04-agent-architecture.md) |
| 6 | API Architecture Diagram | [`docs/05`](docs/05-api-architecture.md) |
| 7 | Security Architecture Diagram | [`docs/06`](docs/06-security-architecture.md) |
| 8 | Terraform Structure | [`infra/`](infra/) + [`docs/02`](docs/02-physical-architecture.md) |
| 9 | Repository Structure | §2 above |
| 10 | Deployment Runbook | [`docs/10`](docs/10-deployment-runbook.md) |
| 11 | Cost Estimate | [`docs/08`](docs/08-cost-estimate.md) |
| 12 | Future-State Enterprise Roadmap | [`docs/11`](docs/11-future-state-roadmap.md) |
| — | Data Model | [`docs/data-model.md`](docs/data-model.md) |
| — | Data Governance Strategy | [`docs/09`](docs/09-data-governance.md) |
| — | Architecture Decision Records | [`docs/adr/`](docs/adr/) |

---

## 4. Build Status

This repository is built **incrementally**. Status of each increment:

- [x] **Increment 1 — Foundation:** repo skeleton, executive overview, service mapping, data model, cost estimate, anchor ADRs.
- [x] **Increment 2 — Infrastructure:** 10 Terraform modules + dev/test/prod environments (validated, `fmt`+`validate` clean). Physical architecture doc.
- [x] **Increment 3 — Product 1:** generator (✅ invariant-tested), Beam pipeline (✅ DirectRunner + 10 unit tests), BQ schemas/DDL, DaaS API (✅ FastAPI/OpenAPI), ADK conversational agent (✅ offline grounding). Data-flow/API/agent docs.
- [x] **Increment 4 — Product 2:** loan DDL + append-only audit tables, full Cloud Workflows orchestration (HITL callback), 5 ADK agents (✅ offline orchestration), loan API (✅ 6 risk tests + flow test). docs/04 expanded.
- [x] **Increment 5 — UX:** SPA + BFF proxy on Cloud Run; Customer / Employee / Admin views with persona simulation (✅ all 3 views rendered + loan-submit verified in preview).
- [x] **Increment 6 — Platform:** CI/CD (GitHub Actions: ci/build-deploy/infra), Cloud Build, Workload Identity Federation (keyless), dev→test→prod promotion. Remaining docs filled: 01 logical, 06 security, 09 governance, 10 runbook, 11 roadmap — **all 12 deliverables complete**.
- [x] **Increment 7 — AgentOps:** eval datasets (txn + labeled loan), offline eval pipeline (✅ runs: grounding 1.0, hallucination 0.0, tool-use 1.0, approval acc 0.875) gated in CI, Vertex eval sketch, reporting/dashboard strategy. **Build complete.**

---

## 5. Quickstart (after infra increment lands)

```bash
# 1. Authenticate to the sandbox project
gcloud auth application-default login
gcloud config set project strongsville-city-schools

# 2. Provision dev
cd infra/envs/dev && terraform init && terraform apply

# 3. Generate synthetic transactions (≤10k/run, ≤4/customer)
python products/transactions/generator/generate.py --count 5000

# Full steps: docs/10-deployment-runbook.md
```

---

_Built as a reference implementation. All architectural decisions are recorded as ADRs and justified
against modern Google Cloud reference architectures and financial-services best practices._
