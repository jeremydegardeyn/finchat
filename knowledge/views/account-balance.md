---
type: BigQuery View
title: gold.account_balance
description: Current balance per account, derived from posted transactions using the canonical sign convention.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_gold_prod&t=account_balance
tags: [gold, balance, serving]
timestamp: 2026-06-25T00:00:00Z
---

# gold.account_balance

Current balance per account = signed sum of `POSTED` transactions
(`DEPOSIT` positive; `WITHDRAWAL`, `FEE`, `TRANSFER` negative). Serves the DaaS
balance API and the transactions agent.

| Column | Meaning |
|--------|---------|
| `account_id` | FK → [`account`](../tables/account.md). |
| `customer_id`, `currency` | Carried from account. |
| `balance` | Signed sum — see [sign convention](../metrics/net-transaction-amount.md). |
| `last_activity_at` | Max posted `event_time`. |
