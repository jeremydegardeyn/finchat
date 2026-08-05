# 21 — Storage Decision Framework

> Which store for which access pattern, and how to tell before you build. Self-serve: a
> team should be able to answer "where does this data belong" from this page without a
> design review.
>
> This is **not** [07 — Service Selection & Mapping](07-service-selection-and-mapping.md).
> That document answers *"what did we deploy in the sandbox and what is the enterprise
> equivalent"*. This one answers *"given an access pattern, which store is correct"* —
> a question that has the same answer in the sandbox and at F500 scale.

## Start here: three questions

Answer in order. The first one that resolves decides it.

1. **Does a single request need one row (or a narrow contiguous range) in single-digit
   milliseconds?** → **operational store** (Bigtable, Spanner, Firestore)
2. **Does the request scan, aggregate, or join across many rows?** → **BigQuery**
3. **Both, for the same data?** → **both**, with BigQuery as the system of record and a
   deliberately materialized operational copy. This is the common case and it is fine —
   what is not fine is discovering it after building on the wrong one.

Most bad storage decisions come from answering question 2 with an operational store, or
question 1 with a warehouse. FinChat has measured both mistakes.

## The measurement that anchors this

From [ADR-0017](adr/0017-bigtable-hot-path.md), on real FinChat data:

| Access pattern | Bigtable | BigQuery |
|---|---|---|
| Point read — account balance | **1.6 ms** | ~1–2 s |
| Top-5 newest transactions (prefix scan) | **4.2 ms** | ~1–2 s |

**Roughly three orders of magnitude**, for exactly the access patterns an operational API
serves. That gap is not a tuning problem and it does not close with a better query — it is
the difference between a store designed for point access and one designed for scans.

The inverse is equally true and less often stated: the same Bigtable schema cannot answer
*"average transaction value by customer segment last quarter"* at all without a full scan
and client-side aggregation. **Its schema is its row key**, and a row key encodes exactly
one access pattern.

## The decision table

| Store | Use when | Avoid when | Cost shape |
|---|---|---|---|
| **BigQuery** | Analytics, aggregation, joins, ad-hoc SQL, ML, anything an analyst touches. Default system of record. | Sub-100ms point reads on the request path | Per-TB scanned; storage cheap; **$0 idle** |
| **Bigtable** | Very high write throughput, wide rows, time-series and newest-first prefix scans, single-digit-ms point reads at any scale | Anything ad-hoc; multi-row transactions; more than one access pattern per table | Per-node, **~$475/mo/node — no scale-to-zero** |
| **Spanner** | Operational reads *plus* strong consistency, relational schema, and multi-row/multi-region transactions | Pure key-value at extreme scale (Bigtable is cheaper); analytics (BigQuery is cheaper) | Per-node + storage; no scale-to-zero |
| **Firestore** | Document-shaped state, per-user data, mobile/web sync, low-moderate volume | Analytics; large scans; complex joins | Per-operation; **effectively $0 idle** |

## Choosing between the operational stores

The three operational stores are not interchangeable, and the question that separates them
is **not** throughput — it is what the write has to guarantee.

| Ask | Bigtable | Spanner | Firestore |
|---|:-:|:-:|:-:|
| Multi-row atomic transaction | ❌ | ✅ | limited |
| Strong consistency across regions | ❌ | ✅ | ❌ |
| Relational schema + SQL | ❌ | ✅ | ❌ |
| Single-digit ms at very high scale | ✅ | ✅ | ❌ |
| Scale-to-zero cost | ❌ | ❌ | ✅ |

**Rule of thumb.** If the operation must move money — debit one account and credit
another, atomically — that is Spanner. If it must serve a read fast at scale and the write
is an append, that is Bigtable. If it is per-user application state with modest volume,
that is Firestore.

FinChat chose Bigtable ([ADR-0017](adr/0017-bigtable-hot-path.md)) because its hot path is
an append-only ledger read newest-first by account — no cross-row transaction anywhere in
it. A payments ledger with transfers would have gone the other way.

## What FinChat actually runs, and the honest gap

| Layer | Store | Status |
|---|---|---|
| System of record / analytics | BigQuery (Bronze/Silver/Gold) | ✅ deployed |
| Operational hot path | Bigtable (`txn_by_account`, `account_balance`) | ✅ built, **default-off** (no scale-to-zero) |
| Semantic layer | BigQuery views + native property graph | ✅ deployed |
| Transactional operational store | **Spanner** | ❌ **not implemented** |

**Spanner is the gap, and it is the one that matters for the Enterprise Query Hub
question.** FinChat demonstrates the key-value hot path and the analytical store, but not
the relational-transactional middle — so any claim about the consumption-layer decision
rests on the table above rather than on measurement. That is a legitimate thing to say in
a design review; presenting it as proven would not be.

## Anti-patterns seen in practice

- **Warehouse on the request path.** BigQuery serving a per-request balance lookup. Works
  in a demo, falls over at concurrency, and the latency never improves.
- **One Bigtable table, two access patterns.** The row key encodes one. A second pattern
  means a second table, denormalized and written twice — that is the design, not a
  workaround, and it should be a deliberate decision rather than a discovery.
- **Choosing Spanner for scale alone.** If there is no transactional or relational
  requirement, it is more expensive than Bigtable for the same reads.
- **Choosing by idle cost.** Firestore and BigQuery scale to zero; Bigtable and Spanner do
  not. That is a real constraint on a sandbox, but it is a *deployment* decision — do not
  let it pick the architecture. FinChat's answer is to build the correct pattern and ship
  it toggled off, with the enterprise mapping documented.

## Using this in a design review

1. State the access pattern before naming a store — read shape, write shape, latency
   budget, consistency requirement, volume.
2. Walk the three questions at the top.
3. If the answer is "both", say which store is the system of record and how the copy stays
   current. An unstated answer here becomes a reconciliation problem later.
4. Record it as an ADR. [ADR-0017](adr/0017-bigtable-hot-path.md) is the worked example,
   including the measurements and the default-off deployment decision.
