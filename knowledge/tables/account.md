---
type: BigQuery Table
title: silver.account
description: Conformed account dimension. Bridges transactions to customers.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_silver_prod&t=account
tags: [silver, dimension, pii-financial]
timestamp: 2026-06-25T00:00:00Z
---

# silver.account

The bridge entity: transactions carry `account_id`, customers own accounts, so
`account` is the only path between a [`transaction`](transaction.md) and its
[`customer`](customer.md). Partitioned by `DATE(opened_at)`, clustered by
`customer_id, account_type`.

## Schema

| Column | Type | Notes |
|--------|------|-------|
| `account_id` | STRING | Primary key. |
| `account_number` | STRING | `PII_FINANCIAL`. Absent from analyst `dim_account`. |
| `customer_id` | STRING | FK → [`customer`](customer.md). |
| `account_type` | STRING | e.g. CHECKING / SAVINGS. |
| `currency` | STRING | ISO 4217. |
| `status` | STRING | Account lifecycle status. |
| `opened_at` | TIMESTAMP | Partition key. |

## Joins

- `customer_id` → [`customer`](customer.md) (`Account BELONGS_TO Customer`).
- Receives `account_id` from [`transaction`](transaction.md) (`Transaction OCCURS_ON Account`).
