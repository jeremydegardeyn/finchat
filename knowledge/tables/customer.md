---
type: BigQuery Table
title: silver.customer
description: Conformed customer dimension. Direct-PII columns are policy-tagged and excluded from analytics.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_silver_prod&t=customer
tags: [silver, dimension, pii-direct]
timestamp: 2026-06-25T00:00:00Z
---

# silver.customer

One row per customer. `full_name` and `email` carry the `PII_DIRECT` policy tag and
are **structurally omitted** from the analyst-facing [`dim_customer`](../views/dim-customer.md).
Partitioned by `DATE(created_at)`, clustered by `segment, customer_id`.

## Schema

| Column | Type | Notes |
|--------|------|-------|
| `customer_id` | STRING | Primary key. |
| `customer_natural_key` | STRING | Govt-id hash (natural key). |
| `full_name` | STRING | `PII_DIRECT` — not exposed to analytics. |
| `email` | STRING | `PII_DIRECT` — not exposed to analytics. |
| `segment` | STRING | Customer segment — the main analytical grouping dimension. |
| `created_at` | TIMESTAMP | "Customer since" (partition key). |

## Rollups

- [`customer_360`](../views/customer-360.md) denormalizes accounts, transactions,
  overdrafts, and loans per customer (`Customer360 ROLLS_UP Customer`).
