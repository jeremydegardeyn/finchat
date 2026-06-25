---
type: BigQuery View
title: graph.dim_customer
description: Analyst-facing customer dimension — identifiers structurally removed (ADR-0018 perimeter).
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod&t=dim_customer
tags: [analyst, perimeter, dimension]
timestamp: 2026-06-25T00:00:00Z
---

# graph.dim_customer

The de-identified customer dimension exposed to Conversational Analytics.
`SELECT customer_id, segment, created_at FROM silver.customer` — `full_name`, `email`,
and the natural key are intentionally absent.

Part of the [analyst perimeter](../playbooks/analyst-perimeter.md). Join via the
[canonical join paths](../playbooks/analyst-join-paths.md).
