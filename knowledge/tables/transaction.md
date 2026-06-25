---
type: BigQuery Table
title: silver.transaction
description: Cleansed, deduplicated banking transaction ledger. One row per posted/pending/rejected transaction.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_silver_prod&t=transaction
tags: [silver, ledger, pii-financial]
timestamp: 2026-06-25T00:00:00Z
---

# silver.transaction

One row per transaction. De-duplicated in-stream by `idempotency_key`
(Beam `DeduplicatePerKey`); uniqueness is asserted by a DQ datascan.
Partitioned by `DATE(event_time)`, clustered by `account_id, txn_type`.

> **PII:** `amount` and `counterparty_account` carry the `PII_FINANCIAL` policy tag.
> Column-level security applies — see the [analyst perimeter](../playbooks/analyst-perimeter.md).

## Schema

| Column | Type | Notes |
|--------|------|-------|
| `transaction_id` | STRING | Primary key. |
| `idempotency_key` | STRING | Producer-minted natural key; stream-deduped. |
| `account_id` | STRING | FK → [`account`](account.md). |
| `txn_type` | STRING | `DEPOSIT` \| `WITHDRAWAL` \| `TRANSFER` \| `FEE`. |
| `amount` | NUMERIC | `PII_FINANCIAL`. Sign convention applied in metrics, not stored. |
| `currency` | STRING | ISO 4217. |
| `counterparty_account` | STRING | `PII_FINANCIAL`. Absent from the analyst `fact_transaction`. |
| `status` | STRING | `POSTED` \| `PENDING` \| `REJECTED`. |
| `event_time` | TIMESTAMP | Business event time (partition key). |

## Joins

- `account_id` → [`account`](account.md). The transaction has **only** `account_id`,
  not `customer_id` — to reach the customer you must bridge through `account`. This is
  the join that the [knowledge graph](../graph/banking-graph.md) and
  [join paths](../playbooks/analyst-join-paths.md) exist to make explicit for agents.

## Balance sign convention

`DEPOSIT` adds; `WITHDRAWAL`, `FEE`, `TRANSFER` subtract. Only `status = 'POSTED'`
rows count toward balances. Encoded canonically in
[`account_balance`](../views/account-balance.md) and the
[net transaction amount](../metrics/net-transaction-amount.md) metric.
