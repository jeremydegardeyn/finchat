---
type: Playbook
title: Analyst Semantic Perimeter
description: The only relational surface conversational analytics may touch — de-identified dim_/fact_ views (ADR-0018).
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_graph_prod
tags: [governance, security, analyst, adr-0018]
timestamp: 2026-06-25T00:00:00Z
# Machine-readable SSOT — compiled into ui/_okf_context.py by scripts/compile_okf.py.
# Keys are dataset roles (graph|gold|loans) mapped to env datasets at runtime.
perimeter:
  graph: [dim_customer, dim_account, fact_transaction, customer_360, kg_relationships]
  gold: [overdraft_history]
  loans: [loan_status]
---

# Analyst Semantic Perimeter (ADR-0018)

The analyst / Conversational Analytics persona may ground **only** on the curated
`dim_*` and `fact_*` views in `finchat_graph_${ENV}`. These views structurally OMIT
identifier columns (`account_number`, `full_name`, `email`, natural keys) — data
minimization by design, not by prompt instruction. Amounts remain (the analytical
point); column-level security still applies via the source policy tags.

## The perimeter

| Analyst view | Exposes | Deliberately absent |
|--------------|---------|---------------------|
| [`dim_customer`](../views/dim-customer.md) | `customer_id, segment, created_at` | `full_name`, `email`, natural key |
| `dim_account` | `account_id, customer_id, account_type, currency, status, opened_at` | `account_number` |
| `fact_transaction` | `transaction_id, account_id, txn_type, amount, currency, status, event_time` | `counterparty_account` |

## Rules for agents

- **Never** reference physical `silver.*` tables in generated SQL. They are outside
  the perimeter; IAM denies the query, which surfaces as a failed turn.
- Ground the NL model on the **semantic** names above — teaching it silver names makes
  it generate out-of-perimeter SQL that then (correctly) gets denied.
- For joins between these views, use the canonical
  [join paths](analyst-join-paths.md).
