# FinChat — Enterprise Banking Data & AI Platform (GCP Reference Implementation)

[![CI](https://github.com/jeremydegardeyn/finchat/actions/workflows/ci.yml/badge.svg)](https://github.com/jeremydegardeyn/finchat/actions/workflows/ci.yml)
[![Build & Deploy](https://github.com/jeremydegardeyn/finchat/actions/workflows/build-deploy.yml/badge.svg)](https://github.com/jeremydegardeyn/finchat/actions/workflows/build-deploy.yml)
[![Terraform](https://github.com/jeremydegardeyn/finchat/actions/workflows/infra.yml/badge.svg)](https://github.com/jeremydegardeyn/finchat/actions/workflows/infra.yml)

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
| — | Knowledge Catalog (Dataplex) enhancement | [`docs/12`](docs/12-knowledge-catalog.md) |
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
- [x] **Increment 7 — AgentOps:** eval datasets (txn + labeled loan), offline eval pipeline (✅ runs: grounding 1.0, hallucination 0.0, tool-use 1.0, approval acc 0.875) gated in CI, Vertex eval sketch, reporting/dashboard strategy.
- [x] **Increment 8 — Model Armor + custom domain:** Model Armor template + BFF prompt/response screening (ADR-0008); Cloud Run custom-domain module for `finchat.datadinosaur.com`.
- [x] **Increment 10 — Knowledge Catalog & Data Products (Dataplex):** discovery/metadata/AI-context overlay ([docs/12](docs/12-knowledge-catalog.md), [ADR-0011](docs/adr/0011-dataplex-universal-catalog.md)) — `catalog` Terraform module (aspect types incl. **data-contract**, domain entry groups, per-product profile + DQ scans, scan-SA fine-grained reader; `enable_catalog` toggle), `discover_data_product` agent tool. **Each of the 5 products published as a first-class Dataplex Data Product** with **contracts** (`contracts/*.yaml` + aspect), **insights** (profile/DQ → operational aspect), and **access groups** (consumer personas + approver, request-to-access). Single source of truth in `scripts/products_catalog.py`; driven by `catalog_bootstrap.py` + `data_products.py` (preview Data Products REST API). Loans dataset made TF-managed/co-located (`us-central1`) to satisfy data-product co-location.
- [x] **Increment 11 — Employee (Analyst) persona:** new persona with (1) **Knowledge Catalog discovery** — search Dataplex assets by description (moved out of the Customer chat into the analyst BFF, returns governed aspects); (2) **Conversational Analytics** — Google's [Conversational Analytics API](docs/adr/0012-conversational-analytics.md) (Gemini Data Analytics) answering NL questions over the data products with generated SQL + results. BFF endpoints `/api/catalog/search` + `/api/analyst/chat`; UI BFF SA granted `dataplex.catalogViewer` + `geminidataanalytics.dataAgentStatelessUser`. ([ADR-0012](docs/adr/0012-conversational-analytics.md))
- [x] **Increment 9 — Live deploy & hardening (dev/test/prod):** CI/CD active (WIF), all services deployed; **agents on Cloud Run** (scale-to-zero, [ADR-0010](docs/adr/0010-agents-on-cloud-run.md)); **RAG** knowledge base via BigQuery `VECTOR_SEARCH` ([ADR-0009](docs/adr/0009-bigquery-vector-rag.md)); **BFF OIDC** to private backends; column-level-security serving grant; customer/account **dimension seed**. DaaS API ✅ live (balance/history/summary 200 on real data). **Build complete + running.**

---

## 4a. Knowledge Catalog & Data Products

Every data product is governed and discoverable on two surfaces: the **Universal
Catalog** (aspects on the BigQuery entry) and a **first-class Dataplex Data
Product** (the console *Data products* page). Per product:

| Facet | Meaning | Source |
|---|---|---|
| **Aspects** | `data-product` (owner/criticality/cert/SLA), `governance` (PII class), `data-contract` (version/SLA/guarantees), `operational` (DQ/insights) | `scripts/catalog_bootstrap.py` |
| **Contracts** | Versioned producer promise — schema, quality, SLAs, access, lineage, deprecation | [`contracts/*.yaml`](contracts/) (code) → `data-contract` aspect |
| **Insights** | Profile + data-quality scan results (row counts, null %, rules → DQ score) | Dataplex datascans → `operational` aspect |
| **Access groups** | Consumer personas (Google groups) that **request access**, approval-gated; per-asset IAM on approval | `scripts/data_products.py` |

The 5 products, their assets, and metadata are a single source of truth in
[`scripts/products_catalog.py`](scripts/products_catalog.py). Bring it up per env:

```bash
cd infra/envs/<env> && terraform apply           # enable_catalog = true
./scripts/run_datascans.sh <env>                 # insights (profile/DQ scans)
python scripts/data_products.py <env>            # data products + assets + access groups
python scripts/catalog_bootstrap.py <env>        # glossary + aspects on table & data-product entries
```

> Aspects are attached to **both** the BigQuery table entry (catalog Search) and the
> data-product entry (the **Data Products page → Aspects tab**). The page's **Contract**
> and **Insights → Query recommendations** tabs use Google's *gated* system aspect types
> and are console-only ("+ Add"/"Edit") — paste-ready contract blurbs + runnable sample
> queries are in [docs/13](docs/13-data-product-console-snippets.md); the same content
> also lives in the `data-contract` aspect + [`contracts/*.yaml`](contracts/).

Full design + diagrams (data-product anatomy, access-request flow, lineage):
[docs/12](docs/12-knowledge-catalog.md).

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
