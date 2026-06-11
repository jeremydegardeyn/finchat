# ADR-0017 — Bigtable hot-path serving tier (operational reads)

- **Status:** Accepted (built + emulator-verified; deploy toggle default **off**)
- **Date:** 2026-06-11
- **Deciders:** Principal Cloud Architect
- **Context tags:** Operational storage, serving latency, Bigtable, dual-tier cost

## Context

The DaaS API serves balances and recent transactions from **BigQuery gold views**.
That is correct for the sandbox and honest about its ceiling: BigQuery is columnar
OLAP — strongly consistent, enormous *per-query* throughput, but a **second-scale
latency floor**, slot-bound concurrency (hundreds of in-flight queries, not 100k QPS),
and per-query cost that makes high-QPS point reads uneconomical. A bank's mobile-app
balance check needs **single-digit-ms point reads at sustained high QPS** — that is
Bigtable's design center (wide-column NoSQL, ~10k QPS/node read throughput, linear
horizontal scaling).

## Decision

Add a **Bigtable hot-path serving tier** behind the unchanged DaaS contract:

- **Tables + row-key design** (the heart of the decision):
  - `txn_by_account`, row key **`{account_id}#{reverse_ts}#{txn_id8}`** — the
    high-cardinality `account_id` prefix spreads writes across tablets (never lead
    with a timestamp: monotonic keys hotspot one tablet); `reverse_ts =
    9999999999 − epoch` makes *newest sort first*, so "latest N transactions" is one
    **prefix scan** that short-circuits after N rows; the txn-id suffix breaks ties.
  - `account_balance`, row key **`{account_id}`** — pure point read; GC keeps 1 version.
  - GC policies: 30-day TTL on hot transactions (BigQuery keeps full history), latest-
    version-only on balances.
- **Read path:** `products/transactions/api/bt.py` — when `BIGTABLE_INSTANCE` is set the
  DaaS balance/transactions endpoints read Bigtable first and **fall back to BigQuery**;
  unset, behavior is unchanged. The API contract does not change (two implementations
  behind one contract — the platform's standing substitution principle, ADR-0002).
- **Write path:** `scripts/bigtable_backfill.py` seeds from BigQuery (idempotent by row
  key); the enterprise tier dual-writes from the Beam pipeline (a `BigtableIO` sink
  branch alongside the BigQuery sink).
- **Cost posture:** Bigtable has **no scale-to-zero** (~$475/mo/node SSD). The Terraform
  module ships **default-off** (`enable_bigtable=false`); development and verification run
  against the **local emulator** (free): `cbtemulator` + `BIGTABLE_EMULATOR_HOST`.

## Verified (emulator, real dev data)

22,042 silver transactions + 5,542 balances backfilled; through the API's own `bt.py`:
**point-read balance 1.6 ms**, **top-5 prefix scan 4.2 ms**, **newest-first ordering
confirmed** — versus the ~1–2s BigQuery path. (Emulator latencies are local, but the
access patterns — point read and bounded prefix scan — are what carry to production.)

## Consistency notes (precision matters)

- BigQuery: **strongly consistent** for committed data; its serving limits are latency/
  concurrency/cost — *not* consistency.
- Bigtable: **strongly consistent within a single cluster**; multi-cluster
  **replication is eventually consistent** — app profiles with single-cluster routing
  restore read-your-writes where required.
- The hot path is a **cache-like projection** of the governed source of truth: BigQuery
  (medallion) remains authoritative; Bigtable rows carry a TTL and are rebuildable from
  silver at any time. CLS/policy-tag governance applies at the source; the hot tier is
  reached only via the DaaS API under its service account.

## When this flips on (the decision rule)

Move an access pattern to Bigtable when **(P95 latency SLO < ~500 ms) OR (sustained QPS
beyond slot-concurrency comfort) OR (per-read BigQuery cost > per-node Bigtable cost at
your volume)**. Keep analytics, joins, and ad-hoc questions in BigQuery — Bigtable has no
joins; its schema **is** the row key.

## Alternatives considered

- **Memorystore (Redis):** great read cache, but no range scans by design key and weaker
  durability story for a ledger projection; Bigtable gives both reads *and* the
  time-ordered scan.
- **Spanner:** the right answer when the hot path must be the **system of record**
  (relational, SQL, external consistency, multi-region writes) — i.e., a true ledger.
  Here the hot path is a projection, so Bigtable's simpler model and throughput economics
  win. (Spanner remains the enterprise mapping for the *ledger itself*.)
- **AlloyDB/Cloud SQL:** relational OLTP fits, but scales vertically/with read pools;
  Bigtable's linear horizontal scaling matches "every customer checks their balance."
- **BI Engine over BigQuery:** accelerates analytics, not single-row operational reads.
