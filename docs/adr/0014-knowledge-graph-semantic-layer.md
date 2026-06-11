# ADR-0014 — Knowledge Graph as the semantic grounding layer for conversational AI

- **Status:** Accepted
- **Date:** 2026-06-09
- **Deciders:** Principal Data Architect
- **Context tags:** Conversational AI, semantic layer, BigQuery, data modeling

## Context

Conversational Analytics (Gemini Data Analytics, [ADR-0012](0012-conversational-analytics.md))
was handed the raw analytical tables but **could not join `transaction` to `customer`**:
transactions carry only `account_id` (never `customer_id`), so any per-customer or
per-segment question either failed or produced a wrong/guessed join. NL→SQL has no way to
know the bridge path (`transaction → account → customer`) without being told the model.

## Decision

Introduce a **Knowledge Graph semantic layer** in BigQuery (`finchat_graph_<env>`,
Terraform-managed **views** only) generated from the data model, and ground all analyst
conversational-AI queries on it:

- **`kg_relationships`** — the join schema (from/to table + column + relationship) as data;
  the single source of the model's edges.
- **`kg_nodes` / `kg_edges`** — entity instances and directed relationships (the literal graph).
- **`customer_360`** — a denormalized, **CLS-safe** per-customer rollup (segment + account /
  transaction / overdraft / loan aggregates; no `full_name`/`email`).
- The analyst BFF passes Conversational Analytics **both** the raw entities **and** the graph,
  plus a **`systemInstruction`** that hard-codes the join keys (mirroring `kg_relationships`)
  and tells the model to prefer `customer_360` for per-customer questions.

## Rationale

- **Fixes the join at the source:** the model is told the bridge path explicitly; verified
  live — CA now emits the correct `transaction → account → customer` join.
- **Hybrid, not graph-only:** keeping the raw tables in scope preserves the ability to answer
  granular questions the rollup doesn't pre-aggregate (e.g. monthly fee revenue, amount
  thresholds); the graph adds the easy, join-safe path. Graph-only would be bulletproof on
  joins but limited to `customer_360`'s fixed columns.
- **Governance-preserving:** `customer_360` exposes only `customer_id` + `segment` + aggregates,
  so analyst conversational AI can't surface direct PII even by accident; CLS still applies to
  the raw tables.
- **Zero-cost, IaC:** views over existing data — no storage, no pipeline; co-located in
  `us-central1` so the inline BQ datasource resolves.

## Decision (amended) — native BigQuery property graph added

The views are a *relational encoding* of the graph; BigQuery's native **property graph**
(`CREATE PROPERTY GRAPH` + GQL `GRAPH_TABLE`/`MATCH`) is now layered on top:
`banking_graph` defines Customer/Account/Transaction/Loan nodes and OWNS/ON_ACCOUNT/
REQUESTED edges directly over the silver/loans tables (cross-dataset node tables work;
metadata-only, zero storage cost). GQL traversals verified to match the SQL-join results.
Division of labor: **GQL** for genuine graph analytics (multi-hop, relationship patterns);
the **views + system instruction** stay as Conversational Analytics grounding, since CA
emits SQL, not GQL.

## Consequences

- New `finchat_graph_<env>` dataset (bigquery module) + `products/graph/schemas/graph.sql`
  (`CREATE OR REPLACE VIEW`, idempotent, re-run per env after the medallion is loaded).
- The analyst CA payload gains `account` + `customer_360` + `kg_relationships` table refs and
  a system instruction; UI deploy gains `GRAPH_DATASET` (+ `KB_DATASET`).
- Rollups are recomputed on read (views); fine at this scale. Materialize (scheduled query /
  BI Engine) if the rollup grows hot.

## Alternatives considered

- **Spanner Graph / Neo4j property graph:** the enterprise target for true graph traversal;
  rejected here as over-scoped/costly — NL analytics needs *relationship metadata*, not a graph
  engine.
- **dbt/LookML semantic layer with a metric/relationship registry:** the enterprise target for
  governed metrics + joins; the graph views are the near-zero-cost stand-in.
- **Only enumerate joins in the prompt (no graph views):** rejected — the relationships would
  live only in a string; `kg_relationships` + `customer_360` make them queryable, testable data
  and give a pre-joined fast path.
- **Graph-only datasource for CA:** rejected — loses granular-query flexibility (see Rationale).
