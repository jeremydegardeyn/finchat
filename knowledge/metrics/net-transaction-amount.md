---
type: Metric
title: Net Transaction Amount
description: Signed sum of an account's or customer's posted transactions using the FinChat balance sign convention.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod&t=customer_360
tags: [metric, balance]
timestamp: 2026-06-25T00:00:00Z
---

# Net Transaction Amount

**Definition:** `SUM(DEPOSIT) − SUM(WITHDRAWAL + FEE)` over posted transactions.
`TRANSFER` is treated as an outflow for running-balance purposes but is excluded
from the net-amount rollup in [`customer_360`](../views/customer-360.md) — follow the
view, not intuition.

**Sign convention (canonical):** `DEPOSIT` is positive; `WITHDRAWAL`, `FEE` are
negative. Only `status = 'POSTED'` rows count.

**Grain:** per `account_id` ([`account_balance`](../views/account-balance.md)) or per
`customer_id` ([`customer_360`](../views/customer-360.md)).
