# ADR-0003 — Dataflow run on-demand (Flex Template) instead of pinned 24/7 streaming

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** Real-time processing, cost engineering, Apache Beam

## Context

Real-time ingestion uses **Apache Beam on Dataflow**. A 24/7 streaming job keeps workers (and
Streaming Engine) running continuously — real, ongoing cost — which conflicts with the near-zero-cost
mandate. The synthetic generator, however, produces transactions **per execution** (≤10k/run), so
demand is bursty, not continuous.

## Decision

Package the Beam pipeline as a **Dataflow Flex Template**. Launch it **on-demand per generation run**
in streaming mode; **drain it on completion** so no workers idle. Cap `max_num_workers` and do not pin
Streaming Engine. Provide a Pub/Sub→BigQuery direct subscription as an even-cheaper fallback path.

## Rationale

- **Same code path as enterprise.** The pipeline (DLP, validation, enrichment, DLQ routing,
  windowed aggregation) is identical to what would run 24/7; only the *job lifetime* differs.
- **Near-zero idle.** Workers exist only during a run (~minutes), then drain. Idle cost ≈ $0.
- **Demonstrates the real transforms** a bank needs in-flight — not just a pass-through subscription.
- **Honest migration story:** flipping to enterprise = run the same template as a persistent streaming
  job with autoscaling + Streaming Engine. No code change.

## Consequences

- The deployment runbook launches the template as part of the generation step (or via the loan
  workflow / Scheduler), and drains it after.
- For the absolute cheapest demo, a **Pub/Sub BigQuery subscription** can replace Dataflow for the
  raw Bronze write; Silver/Gold transforms then run as scheduled BigQuery SQL. Documented as an option.
- Slightly higher per-run latency (worker spin-up ~1–2 min) vs. an always-warm job — acceptable in
  sandbox; the enterprise toggle removes it.

## Enterprise mapping

| Aspect | Sandbox | Enterprise |
|---|---|---|
| Job lifetime | Per-run, drained | Persistent streaming |
| Workers | Capped, ephemeral | Autoscaled |
| Streaming Engine | Off | On |
| Code | identical Flex Template | identical Flex Template |
