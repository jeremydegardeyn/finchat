---
type: Property Graph
title: banking_graph
description: Native BigQuery property graph over the operational entities, queried with GQL for multi-hop traversals.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod
tags: [graph, gql, semantic-layer]
timestamp: 2026-06-25T00:00:00Z
---

# banking_graph

A native BigQuery property graph (`GRAPH_TABLE ... MATCH`, GQL) defined over the
existing silver/loan tables — metadata only, no data copied, no extra storage.
Serves native graph analytics (multi-hop paths, fraud-ring-style traversals).

## Entity–relationship model

```
Customer (customer_id)
  └─OWNS→ Account (account_id, customer_id)
            ├─ON_ACCOUNT← Transaction (account_id)
            └─REQUESTED→  Loan (account_id)
```

| Node | Key | Source |
|------|-----|--------|
| `Customer` | `customer_id` | [`silver.customer`](../tables/customer.md) |
| `Account` | `account_id` | [`silver.account`](../tables/account.md) |
| `Transaction` | `transaction_id` | [`silver.transaction`](../tables/transaction.md) |
| `Loan` | `loan_id` | `silver…loan_request` |

| Edge | From → To |
|------|-----------|
| `OWNS` | Customer → Account |
| `ON_ACCOUNT` | Transaction → Account |
| `REQUESTED` | Account → Loan |

PII (`full_name`, `email`) is intentionally excluded — graph analytics needs ids
and segments, not identities.

## Example (GQL)

```sql
SELECT segment, COUNT(*) AS n
FROM GRAPH_TABLE(`…finchat_graph_prod.banking_graph`
  MATCH (c:Customer)-[:OWNS]->(a:Account)<-[:ON_ACCOUNT]-(t:Transaction)
  COLUMNS (c.segment AS segment))
GROUP BY segment;
```

## Relationship to Conversational Analytics

CA emits **SQL, not GQL**, so it is grounded by the relational
[join paths](../playbooks/analyst-join-paths.md) (the `kg_relationships` view), not by
this property graph. Two different jobs: traversal vs. NL-to-SQL grounding.
