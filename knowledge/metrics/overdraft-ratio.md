---
type: Metric
title: Overdraft Ratio
description: Share of an account's posted transactions that occurred while the running balance was negative.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_gold_prod&t=overdraft_history
tags: [metric, risk]
timestamp: 2026-06-25T00:00:00Z
---

# Overdraft Ratio

**Definition:** `overdraft_events / NULLIF(total_posted_transactions, 0)` per account.

**Grain:** one value per `account_id`.

**Source:** [`gold.overdraft_history`](../views/overdraft-history.md), which derives a
running balance over `POSTED` [transactions](../tables/transaction.md) ordered by `event_time`.

**Used by:** Loan Approval risk scoring (`risk_assessment.factors` reason codes).

**Do not** re-derive from raw transactions in an ad-hoc query — use this canonical
view so the streaming product and the loan product agree on the number.
