---
type: BigQuery Table
title: loans.loan_request
description: One row per loan application. Links the lending product to the transactions product via account_id.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_loans_prod&t=loan_request
tags: [loans, request, pii-direct]
timestamp: 2026-06-25T00:00:00Z
---

# loans.loan_request

The entry point of the Loan Approval workflow. `account_id` is the cross-product link
to the transactions domain — it's how loan risk scoring reaches
[`overdraft_history`](../views/overdraft-history.md). Partitioned by `DATE(submitted_at)`,
clustered by `status, customer_name`.

## Schema

| Column | Type | Notes |
|--------|------|-------|
| `loan_id` | STRING | Primary key (UUID). |
| `customer_name` | STRING | `PII_DIRECT`. |
| `account_id` | STRING | Nullable FK → [`account`](account.md) (cross-product link). |
| `amount` | NUMERIC | `PII_FINANCIAL`. |
| `term_months` | INT64 | Loan term. |
| `status` | STRING | CREATED \| PROFILED \| REVIEWED \| RECOMMENDED \| PENDING_APPROVAL \| APPROVED \| REJECTED \| MODIFIED. |
| `submitted_at` | TIMESTAMP | Partition key. |

## Downstream

- Risk + decisions roll up into [`loan_status`](../views/loan-status.md).
- Appears in the [knowledge graph](../graph/banking-graph.md) as `Loan`
  (`Account REQUESTED Loan`).
