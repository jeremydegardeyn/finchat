---
type: BigQuery View
title: loans.loan_status
description: Current state of each loan — latest risk assessment + latest approval decision folded onto the request.
resource: https://console.cloud.google.com/bigquery?p=strongsville-city-schools&d=finchat_loans_prod&t=loan_status
tags: [loans, serving, analyst]
timestamp: 2026-06-25T00:00:00Z
---

# loans.loan_status

The serving view for the Loan Approval product and the only loans surface in the
[analyst perimeter](../playbooks/analyst-perimeter.md). Collapses the append-only,
versioned `risk_assessment` and `approval_decision` history to the **latest** row per
loan (`ROW_NUMBER() … ORDER BY version DESC`) and joins them onto
[`loan_request`](../tables/loan-request.md).

## Key columns

| Column | Meaning |
|--------|---------|
| `loan_id` | PK → [`loan_request`](../tables/loan-request.md). |
| `status` | Workflow state (CREATED → … → APPROVED/REJECTED). |
| `risk_score`, `recommendation` | Latest `risk_assessment` (0=best…100=worst; APPROVE/REVIEW/DECLINE). |
| `reasons`, `factors` | Explainability — prose + structured factor attributions (ADR-0013). |
| `final_decision`, `approver`, `decided_at` | Latest `approval_decision` (append-only audit trail). |

History is never mutated — `approval_decision` is INSERT-only, so the full decision
trail is reconstructable. This view only surfaces the current state.
