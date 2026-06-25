---
type: BigQuery Dataset
title: Banking Transactions (Medallion)
description: Streaming ledger data product — Pub/Sub → Dataflow → BigQuery bronze/silver/gold → DaaS APIs → ADK agent.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools
tags: [data-product, medallion, streaming]
timestamp: 2026-06-25T00:00:00Z
---

# Banking Transactions

A first-class Dataplex **Data Product** with a published data contract
(`contracts/transactions.yaml`), profile + DQ datascans, and access groups.

## Lineage (one direction of flow)

`Pub/Sub topic` → `Dataflow (Beam, idempotency-keyed dedup)` →
[`bronze.transaction_event`](../tables/transaction-event.md) →
[`silver.customer`](../tables/customer.md) / [`silver.account`](../tables/account.md) /
[`silver.transaction`](../tables/transaction.md) →
gold serving views ([`account_balance`](../views/account-balance.md),
[`overdraft_history`](../views/overdraft-history.md)).

## Cross-product lineage

[`overdraft_history`](../views/overdraft-history.md) feeds the **Loan Approval**
product's risk evaluation — a deliberate cross-product join, documented so agents
understand why a transactions view is read during loan scoring.

## Datasets (per environment `${ENV}` = dev|test|prod)

- `finchat_bronze_${ENV}` — raw immutable landing.
- `finchat_silver_${ENV}` — cleansed, conformed, PII-tagged.
- `finchat_gold_${ENV}` — business aggregates / serving views.
- `finchat_graph_${ENV}` — property graph + analyst perimeter.
