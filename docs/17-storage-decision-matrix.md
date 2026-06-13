# 17 — GCP Storage Decision Matrix (banking workloads)

Picking the right datastore is a lead-architect decision, and GCP gives you six serious
options that overlap just enough to be dangerous. This is the matrix I reason from:
what each store *is*, where it wins, where it quietly bankrupts you or misses an SLO,
and which banking workload each one owns. FinChat itself uses two of them deliberately —
**BigQuery for analytics, Bigtable for serving** — which is the canonical split.

## The decision in seven questions

Before naming a product, answer these. They almost always pick the store for you:

1. **Access pattern** — OLTP transactions, OLAP analytics, low-latency point serving, or a cache?
2. **Consistency** — external/strong (a ledger must never lose a cent) or eventual (a feed can lag)?
3. **Latency + throughput SLO** — single-digit ms at millions QPS, or seconds is fine?
4. **Data model** — relational, wide-column key/value, document, columnar?
5. **Geography** — globally distributed with strong consistency, or single-region?
6. **Cost shape** — provisioned nodes (pay for capacity 24/7) or serverless / scale-to-zero (pay per use)?
7. **Operational fit** — does the team live in Postgres? Is "no nodes to manage" a hard requirement?

## The matrix

| Store | Model | Consistency | Latency | Scale | Cost shape | Scale‑to‑zero |
|---|---|---|---|---|---|---|
| **Bigtable** | Wide‑column NoSQL (HBase API) | Strong per row (single cluster); eventual across replicas | single‑digit ms | millions QPS, linear by node | provisioned **nodes** (~$0.65/node‑hr) + storage | ❌ (min 1 node) |
| **Spanner** | Relational, globally distributed; SQL | **External consistency** (TrueTime), ACID across regions | low‑ms | near‑unbounded, horizontal | provisioned **nodes / 100‑PU units** + storage; premium | ❌ (min 100 PU) |
| **AlloyDB** | PostgreSQL‑compatible; OLTP + columnar (HTAP) | Strong (single primary) | low‑ms | vertical + read pools; regional | provisioned **vCPU/mem** + storage | ❌ |
| **Firestore** | Document NoSQL; realtime + offline sync | Strong per doc; ACID multi‑doc txns | ms | automatic | **serverless**: pay per op + storage | ✅ |
| **BigQuery** | Columnar analytical warehouse; SQL | Strong on committed data | **seconds** (OLAP) | petabyte, separated storage/compute | **serverless** on‑demand ($/TB scanned) or slot reservations | ✅ (compute) |
| **Memorystore** | In‑memory Redis / Memcached | Cache semantics (not a system of record) | **sub‑ms** | by instance memory | provisioned **memory/hr** | ❌ |

*(Cost figures are order‑of‑magnitude and region‑dependent — confirm live pricing. The point
is the **shape**: nodes/PU/memory = you pay for capacity around the clock; serverless = you pay
per query/op and idle is ~free.)*

## What each one is actually for

- **Bigtable** — high‑throughput, low‑latency serving and time‑series. The row key *is* the
  design: lexicographically sorted, so monotonic keys (timestamps, sequential IDs) create
  hotspots — you salt, field‑promote, or reverse‑timestamp instead. No joins, no rich secondary
  indexes. *Banking:* real‑time balance/transaction serving, fraud feature lookups, payment
  telemetry. *Avoid for:* ad‑hoc analytics, relational joins, tiny low‑traffic apps (you still pay
  for a node).
- **Spanner** — the relational database that doesn't make you choose between consistency and
  horizontal scale. TrueTime gives external consistency across regions; interleaved tables give
  locality; change streams give CDC. PKs must avoid hotspots (UUIDv4 / hashed / bit‑reversed, never
  a monotonic sequence). *Banking:* the **payments / double‑entry ledger**, core accounts,
  anything global that must be correct. *Avoid for:* small apps (cost floor) and pure analytics.
- **AlloyDB** — PostgreSQL when Cloud SQL runs out of headroom: a columnar engine for real‑time
  analytics on the same OLTP data (HTAP), plus pgvector + ScaNN for in‑database vector search.
  *Banking:* loan‑origination and operational systems, Postgres‑native RAG/vector. *Avoid for:*
  global strong consistency at Spanner scale, or when you need scale‑to‑zero.
- **Firestore** — serverless document store with realtime listeners and offline sync, built for
  client apps. Scales to zero. *Banking:* mobile/web banking app state, customer profiles,
  notification fan‑out, real‑time UI. *Avoid for:* analytics, complex relational queries, big
  aggregations.
- **BigQuery** — the serverless analytical warehouse. OLAP, not serving: query latency is seconds,
  and it is the wrong tool for high‑QPS single‑row reads or row‑level OLTP. *Banking:* the
  medallion lake/warehouse, regulatory reporting (BCBS 239), ML feature engineering at rest, BI.
  *Avoid for:* anything a user is waiting on per‑row in real time.
- **Memorystore** — managed Redis/Memcached as a cache, not a source of truth. *Banking:* session
  store, rate limiting, idempotency keys, fraud **velocity counters**, hot‑feature cache. *Avoid
  for:* durable storage of record.

## Banking workload → store

| Workload | Store | Why |
|---|---|---|
| Real‑time balance & transaction serving (high QPS, <10 ms) | **Bigtable** | wide‑column serving at scale; reverse‑timestamp row keys for newest‑first scans |
| Payments / double‑entry ledger (global, strongly consistent) | **Spanner** | external consistency + horizontal scale; correctness is non‑negotiable |
| Analytics, medallion, BCBS 239 reporting, ML features, BI | **BigQuery** | serverless OLAP at petabyte scale; governance/CLS plane |
| Mobile/web banking app state, profiles, notifications | **Firestore** | serverless, realtime, offline sync, scale‑to‑zero |
| Loan origination / operational app (Postgres + vector/RAG) | **AlloyDB** | high‑perf Postgres + HTAP + pgvector (Cloud SQL for smaller) |
| Session cache, rate limiting, idempotency, fraud velocity counters | **Memorystore** | sub‑ms in‑memory; ephemeral by design |

## How FinChat applies it

FinChat runs the canonical two‑store split:

- **BigQuery** is the analytical backbone — the bronze/silver/gold medallion, the governed data
  products, the graph and eval datasets. Everything that's *analyzed* lives here.
- **Bigtable** is the hot‑path serving tier ([ADR‑0017](adr/0017-bigtable-hot-path.md)): the DaaS
  API reads balances and recent transactions from Bigtable (`account_id#reverse_ts` row keys for
  newest‑first prefix scans) and falls back to BigQuery only when the hot store misses.

The line to say out loud: *"BigQuery is where data goes to be analyzed; Bigtable is where data goes
to be served. Forcing one to do the other's job is the most common GCP storage mistake."*

## Interview traps to pre‑empt

- **"Why not just BigQuery for everything?"** — It's OLAP. Seconds of latency, billed per byte
  scanned, no per‑row serving. Great warehouse, wrong serving layer.
- **"Bigtable vs Spanner?"** — Different jobs. Bigtable is NoSQL scale and speed with no
  cross‑row transactions; Spanner is relational with external consistency at global scale — the
  ledger. Reach for Bigtable for throughput, Spanner for correctness.
- **"AlloyDB vs Cloud SQL vs Spanner?"** — Cloud SQL is managed lift‑and‑shift Postgres/MySQL;
  AlloyDB is high‑performance Postgres with HTAP (still regional, Postgres app model); Spanner is a
  different engine entirely — global horizontal scale, not Postgres‑compatible.
- **Hotspots** — for both Bigtable and Spanner, monotonic keys are the classic foot‑gun. Name the
  fix (salting / field promotion / reverse timestamp / bit‑reversed sequences) before they ask.
- **Cost discipline** — match the cost shape to the traffic. Provisioned (Bigtable/Spanner/AlloyDB)
  earns its keep under steady high load; serverless (BigQuery/Firestore) wins for spiky or low
  volume. FinChat's near‑zero‑cost sandbox leans on serverless + emulators precisely for this
  reason, and documents the enterprise upgrade path rather than paying for it idle.
