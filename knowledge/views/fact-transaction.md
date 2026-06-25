---
type: BigQuery View
title: graph.fact_transaction
description: Analyst-facing transaction fact — counterparty removed (ADR-0018 perimeter).
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod&t=fact_transaction
tags: [analyst, perimeter, fact]
timestamp: 2026-06-25T00:00:00Z
---

# graph.fact_transaction

The transaction fact exposed to Conversational Analytics:
`transaction_id, account_id, txn_type, amount, currency, status, event_time`.
`counterparty_account` is intentionally absent.

Carries only `account_id` — reach the customer by bridging through `dim_account`
(see [join paths](../playbooks/analyst-join-paths.md)). Part of the
[analyst perimeter](../playbooks/analyst-perimeter.md).
