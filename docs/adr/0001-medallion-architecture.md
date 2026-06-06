# ADR-0001 — Medallion (Bronze/Silver/Gold) architecture on BigQuery + BigLake

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** Data architecture, data as a product, governance

## Context

Curated banking data must be analytics-ready, governable, replayable, and serveable to APIs/agents.
We need a layering strategy that separates raw capture, conformance/governance, and business serving.

## Decision

Adopt the **Medallion architecture** with three BigQuery datasets — `finchat_bronze`, `finchat_silver`,
`finchat_gold` — with **BigLake** managing raw Bronze data on Cloud Storage (cheap retention + fine-
grained ACLs) and acting as the unified governance surface.

## Rationale

- **Bronze (raw, immutable):** never lose data; enable replay/reprocessing after logic changes;
  satisfy audit "as-received" requirements for a regulated ledger.
- **Silver (conformed, governed):** apply quality, schema enforcement, dedup (idempotency-key
  `MERGE`), and **PII de-identification once**, so all downstream consumers inherit trusted, masked data.
- **Gold (serving):** business-shaped aggregates (balances, summaries) for low-latency API/agent reads
  without re-deriving logic per query, exposing only purpose-fit governed data.

## Consequences

- Clear contracts and ownership per layer (data-as-a-product).
- Higher storage footprint (data lives 3×) — mitigated by partition expiration + BigLake/GCS cold
  tiering for Bronze (see [data-model](../data-model.md), [cost](../08-cost-estimate.md)).
- Reprocessing = re-run Silver/Gold builds from Bronze; no re-ingestion needed.

## Alternatives considered

- **Two-tier (raw + serving):** rejected — conflates conformance with serving, weak governance.
- **Single curated table:** rejected — no replay, poor lineage, governance applied per-consumer.
