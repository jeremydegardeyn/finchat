---
type: Bundle
title: FinChat Knowledge Bundle
description: Curated, agent-readable context for the FinChat banking Data & AI platform — schemas, metric definitions, join paths, and the de-identified analyst perimeter.
resource: https://github.com/jeremydegardeyn/finchat
tags: [finchat, banking, data-ai, okf]
timestamp: 2026-06-25T00:00:00Z
---

# FinChat Knowledge Bundle

An [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
bundle: the curated, organization-specific context FinChat's AI agents need but a
foundation model cannot know — table semantics, metric definitions, join paths, and
the boundaries of what analytics may touch.

This bundle is the *grounding source of truth*. It is authored alongside code and
versioned in git, then ingested by the **Knowledge Catalog** and served to the
Conversational Analytics router and the RAG / ADK agents.

## Data products

- [Banking Transactions](datasets/transactions.md) — streaming ledger, BigQuery Medallion.
- Loan Approval — long-running agentic HITL workflow (see [`loan_request`](tables/loan_request.md)).

## Layers

- **Bronze** — raw immutable Pub/Sub landing (replay/audit).
- **Silver** — cleansed, conformed, PII-tagged operational tables.
- **Gold** — business serving views for APIs and agents.
- **Graph** — native property graph + the [analyst semantic perimeter](playbooks/analyst-perimeter.md).

## How agents should use this bundle

1. Resolve the question to **concepts** (tables, metrics, the graph).
2. For analytics, ground ONLY on the [analyst perimeter](playbooks/analyst-perimeter.md)
   `dim_*` / `fact_*` views and the [join paths](playbooks/analyst-join-paths.md) — never
   physical `silver` tables (IAM denies out-of-perimeter SQL by design).
3. For metrics, use the canonical definitions in [`metrics/`](metrics/overdraft-ratio.md) —
   do not re-derive.
