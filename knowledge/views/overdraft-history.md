---
type: BigQuery View
title: gold.overdraft_history
description: Per-account overdraft profile derived from a running posted balance. Feeds loan risk scoring.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_gold_prod&t=overdraft_history
tags: [gold, risk, cross-product]
timestamp: 2026-06-25T00:00:00Z
---

# gold.overdraft_history

Computes a running balance per account over `POSTED` transactions (window ordered by
`event_time`) and summarizes how often it went negative. **Consumed by the Loan
Approval product** — this is documented cross-product lineage so agents know why a
transactions-domain view is read during credit evaluation.

## Output columns

| Column | Meaning |
|--------|---------|
| `account_id` | FK → [`account`](../tables/account.md). |
| `overdraft_events` | Count of points where running balance < 0. |
| `lowest_balance` | Minimum running balance ever reached. |
| `overdraft_ratio` | `overdraft_events / total posted txns` — see [metric](../metrics/overdraft-ratio.md). |
