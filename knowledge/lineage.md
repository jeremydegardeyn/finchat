---
type: Lineage
title: End-to-End Lineage
description: Where data comes from, what it feeds, and the cross-product dependencies an agent should understand.
reviewed: 2026-07-27
review_by: 2027-01-27
owner: data-platform@datadinosaur.com
---

# End-to-End Lineage

Traceability is a BCBS 239 obligation, but it is also grounding: an agent that knows a view
is *derived* will not treat it as an independent source of truth.

## Transactions — the streaming path

```
Pub/Sub topic
  └─ Dataflow (Beam) — idempotency-keyed dedup
       └─ bronze.transaction_event          raw, immutable, replayable
            └─ silver.customer / account / transaction    cleansed, conformed, PII-tagged
                 ├─ gold.account_balance               signed sum of POSTED rows
                 ├─ gold.overdraft_history             running balance → overdraft events
                 └─ graph.dim_* / fact_*               de-identified analyst perimeter
                      └─ graph.customer_360            pre-joined per-customer rollup
```

Every layer below silver is a **view**. No independent copy exists, which is why there is
one deletion path and why "gold is stale" is not a failure mode here.

## Loans — the agentic path

```
loan_request  (customer submits)
  ├─ reads gold.overdraft_history      ← cross-product dependency
  ├─ risk_assessment      append-only, versioned
  └─ approval_decision    append-only, versioned, human approver
       └─ loans.loan_status            latest-state serving view
```

## The cross-product dependency that matters

**Loan risk reads a transactions-domain view.** `overdraft_history` is owned by the
deposits domain and consumed by lending. That is deliberate and documented, and it has two
consequences an agent and a reviewer both need:

1. A change to the overdraft calculation **changes credit outcomes.** It is not a local change.
2. Under BCBS 239 this is exactly the lineage hop that must be traceable — a risk number
   depending on a deposits view, with a named owner on each side.

## Derived vs. authoritative

| Kind | Examples | Agent behaviour |
|---|---|---|
| **Authoritative** | `silver.customer`, `silver.account`, `silver.transaction`, `loan_request` | The record. Not on the analyst surface. |
| **Derived view** | `account_balance`, `overdraft_history`, `dim_*`, `fact_*`, `customer_360`, `loan_status` | Answer from these; never describe them as the source of record. |
| **Replay only** | `bronze.transaction_event` | Never an answering surface. |

## Honest gap

This file is hand-maintained prose. Column-level lineage is captured by the platform's
catalog; the two are not generated from one another. A future pass should render this
diagram from the catalog's lineage graph so it cannot drift.
