---
type: Playbook
title: Analyst Join Paths
description: Canonical join keys passed to Conversational Analytics so it generates correct, in-perimeter SQL.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod&t=kg_relationships
tags: [analyst, joins, grounding, conversational-analytics]
timestamp: 2026-06-25T00:00:00Z
# SSOT moved to knowledge/ontology.yaml (Inc 20): the join model now lives there as
# `relationships:` and is projected into the CA join bullets (ui/_okf_context.py) AND
# the kg_relationships view. This playbook is now human documentation only.
---

# Analyst Join Paths

These are the relationships fed into the Conversational Analytics `systemInstruction`
(the `kg_relationships` view as data). They solve the **transaction→customer join
problem**: a [`fact_transaction`](../views/fact-transaction.md) row carries only
`account_id`, so reaching the customer requires bridging through `dim_account`.
Without this, CA hallucinated a direct `customer_id` on transactions.

## Join schema (semantic names only)

| From | Key | To | Relationship |
|------|-----|----|--------------|
| `dim_account` | `customer_id` | `dim_customer` | Account BELONGS_TO Customer |
| `fact_transaction` | `account_id` | `dim_account` | Transaction OCCURS_ON Account |
| `overdraft_history` | `account_id` | `dim_account` | OverdraftProfile SUMMARIZES Account |
| `customer_360` | `customer_id` | `dim_customer` | Customer360 ROLLS_UP Customer |

## Canonical worked example

> "Net transaction amount by customer segment"

```
fact_transaction →(account_id)→ dim_account →(customer_id)→ dim_customer
GROUP BY dim_customer.segment
```

Use the [Net Transaction Amount](../metrics/net-transaction-amount.md) sign convention.
For per-customer rollups that already pre-join all of this, prefer
[`customer_360`](../views/customer-360.md).
