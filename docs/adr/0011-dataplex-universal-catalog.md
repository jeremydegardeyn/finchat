# ADR-0011 — Dataplex Universal Catalog as the enterprise discovery / metadata / AI-context layer

- **Status:** Accepted
- **Date:** 2026-06-08
- **Deciders:** Principal Data Architect
- **Context tags:** Governance, data mesh, AI readiness, metadata

## Context

FinChat has strong *enforcement* governance (policy tags + CLS, RLS, DLP, audit, least-privilege IAM,
inline lineage columns) but lacks a unified **discovery / metadata / semantic** plane. Business users,
analysts, developers, and AI agents must know physical dataset/table names to find data, and there's
no business-domain organization, glossary, certification, or DQ-at-discovery. As the mesh grows
(Customer, Deposits, Lending, Payments, Fraud, Risk, …) this becomes the bottleneck.

## Decision

Adopt **Dataplex Universal Catalog** ("Knowledge Catalog") as the **primary enterprise discovery,
metadata, governance-visibility, and AI-context layer** — as an **overlay** over the existing
lakehouse, with **no change** to BigQuery storage, the medallion pipelines, the serving APIs, or the
agent runtime. Model **business domain → data product** via Entry Groups + a Data Product **aspect**;
define **Aspect Types** for product/governance/operational/AI metadata; add a **Business Glossary**;
publish **Data Scan** DQ scores and **Data Lineage** into the catalog; and add a catalog-backed
`discover_data_product` agent tool so agents resolve **business concepts → governed output ports**.

## Rationale

- **Augment, don't redesign:** the catalog harvests existing assets; enforcement stays in policy
  tags/IAM (one source of truth, two surfaces — enforce + discover).
- **Mesh-native:** domains/data products/output ports + certification make data-as-a-product real and
  self-service.
- **AI semantic layer:** agents bind to catalog-resolved, certified, DQ-gated output ports instead of
  hard-coded table names — better grounding and governance for RAG/agents.
- **Modern model:** Aspects/Aspect Types + Glossary + Data Scans + Lineage API are the current Dataplex
  primitives (successors to Data Catalog tag templates), with Analytics Hub for federated sharing.

## Decision (extended) — first-class Data Products, contracts, insights, access groups

Beyond the metadata overlay, publish each product as a **first-class Dataplex Data
Product** (preview `dataplex.googleapis.com/v1/.../dataProducts`) with four facets:
**aspects** (incl. a new **`data-contract`** aspect type), **contracts** authored as
code in [`contracts/*.yaml`](../../contracts/), **insights** (per-product profile
scans + a detailed quality scan → `operational` aspect), and **access groups**
(consumer personas + approver emails; consumers *request access*, IAM is granted on
approval). The 5 products are a single source of truth in
[`scripts/products_catalog.py`](../../scripts/products_catalog.py).

Two sub-decisions:
- **Aspect types are `global`**, not regional — BigQuery entries land in the `us`
  multi-region and reject a regional aspect type.
- **Datasets must be co-located with their data product (`us-central1`).** The
  loans dataset was the only one created by raw DDL (`CREATE SCHEMA` defaults to the
  US multi-region); it is now **Terraform-managed** in the bigquery module so it
  can't drift, and the runbook pins `bq query --location`.

## Consequences

- New `infra/modules/catalog` (aspect types incl. `data-contract`, domain entry groups,
  per-product profile + quality data scans, Dataplex scan-SA fine-grained reader on all
  policy tags); `dataplex` + `datalineage` APIs enabled. Glossary, data products, and
  custom lineage events via API/gcloud/REST where Terraform support is nascent (the Data
  Products API has **no** gcloud group or TF resource yet — driven by `scripts/data_products.py`).
- A `discover_data_product` ADK tool (P4) shifts agent grounding to catalog contracts.
- Per-asset IAM binding for access groups requires **real Cloud Identity groups**; it is
  opt-in (`FINCHAT_BIND_ASSET_IAM=1`) so the demo defines the governance model without
  failing on placeholder groups.
- Rollout is phased ([12-knowledge-catalog](../12-knowledge-catalog.md) §10); each phase is
  independently shippable and non-disruptive. Default **off** (enterprise toggle) to
  preserve near-zero sandbox cost.

## Alternatives considered

- **Keep ad-hoc discovery (status quo):** rejected — doesn't scale across domains; no AI semantic layer.
- **Custom metadata DB / homegrown catalog:** rejected — reinvents a managed, lineage/DQ-integrated service.
- **Data Catalog tag templates only (legacy):** rejected — superseded by Aspects; no data-product/glossary model.
