---
type: BigQuery View
title: graph.customer_360
description: Denormalized per-customer rollup of accounts, transactions, overdrafts, and loans. CLS-safe (no direct PII).
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod&t=customer_360
tags: [graph, rollup, analyst]
timestamp: 2026-06-25T00:00:00Z
---

# graph.customer_360

One pre-joined row per customer — the easiest grounding surface for customer-level
analytical questions. Excludes direct PII, so it is safe for the analyst persona.

## Columns

| Column | Source |
|--------|--------|
| `customer_id`, `segment`, `customer_since` | [`customer`](../tables/customer.md) |
| `account_count` | [`account`](../tables/account.md) |
| `transaction_count`, `net_transaction_amount` | [`transaction`](../tables/transaction.md) — see [metric](../metrics/net-transaction-amount.md) |
| `overdraft_events`, `lowest_balance` | [`overdraft_history`](overdraft-history.md) |
| `loan_count`, `total_loan_amount` | `loan_request` |

Prefer this view over manual multi-hop joins when the question is per-customer.
