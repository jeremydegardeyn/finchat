---
type: Reference
title: BIAN alignment
owner: ai-governance@datadinosaur.com
reviewed: 2026-09-12
review_by: 2027-03-12
---

# BIAN alignment

How FinChat's concepts and operations map to the industry's names — the
[BIAN](https://bian.org) Service Landscape's **service domains** and **action terms**.
Use this to answer "which BIAN service domain is X?" or "what does BIAN call this
operation?". The mapping is an **alignment, not an adoption** (ADR-0033): FinChat keeps
its own names, and the `Match` column says how well the two agree. A `closeMatch` is a
concept an integrator can treat as the same thing with the noted caveat; a
`relatedMatch` is not the same thing and should not be presented as one.

Alignment is to the public Service Landscape only. BIAN's Business Object Model is
member-gated and is **not** reproduced here — do not describe BIAN business objects
beyond the names in this table. `Household` is deliberately unmapped: it is not modelled
in FinChat, and giving it a BIAN home would suggest otherwise.

<!-- >>> generated from knowledge/ontology.yaml (scripts/compile_ontology.py) — do not edit by hand -->
Aligned to **BIAN Service Landscape 13** (https://bian.org/servicelandscape-13-0-0/). Match strength uses the SKOS mapping relations: `exactMatch` / `closeMatch` / `relatedMatch`.

### Concepts → service domains

| FinChat concept | Analyst view | BIAN service domain | Match | Note |
|---|---|---|---|---|
| Customer | `dim_customer` | Party Reference Data Directory | `closeMatch` | collapses BIAN Party (the person) + Customer Agreement (the relationship); segment is a relationship property |
| Account | `dim_account` | Current Account · Savings Account (by `account_type`) | `closeMatch` | one class, two BIAN domains — checking -> Current Account, savings -> Savings Account |
| Transaction | `fact_transaction` | Position Keeping | `closeMatch` | the financial position log, POSTED-only here; BIAN also keeps pending items |
| OverdraftProfile | `overdraft_history` | Customer Behavior Insights | `relatedMatch` | a derived behavioural analytic over Position Keeping, not a facility property |
| Loan | `loan_status` | Customer Offer | `closeMatch` | an ORIGINATION record (requested -> approved/denied); a fulfilled loan would be Consumer Loan, not modelled |

### Service domains referenced

| Service domain | Functional pattern | Control record |
|---|---|---|
| Party Reference Data Directory | Directory | Party Reference Data Directory Entry |
| Current Account | Fulfill | Current Account Facility |
| Savings Account | Fulfill | Savings Account Facility |
| Position Keeping | Maintain | Financial Position Log |
| Customer Behavior Insights | Analyze | Customer Behavior Insights Analysis |
| Customer Offer | Process | Customer Offer Procedure |
| Consumer Loan | Fulfill | Consumer Loan Facility — referenced only — a FULFILLED loan is not modelled here |

### Operations → service domain · action term

| API | Layer | Operation | BIAN service domain | Action term | Note |
|---|---|---|---|---|---|
| txn-api | system | `getBalance` | Position Keeping | Retrieve | signed sum of POSTED items in the position log |
| txn-api | system | `getTransactionHistory` | Position Keeping | Retrieve |  |
| txn-api | system | `getRecentActivity` | Position Keeping | Retrieve |  |
| txn-api | system | `getAccountSummary` | Current Account | Retrieve | the facility view; composes over Position Keeping inside the domain |
| loan-api | system | `POST /v1/loans` | Customer Offer | Initiate |  |
| loan-api | system | `GET /v1/loans/{loan_id}` | Customer Offer | Retrieve |  |
| loan-api | system | `GET /v1/loans` | Customer Offer | Retrieve | the officer queue; account_id filter added by ADR-0030 |
| loan-api | system | `GET /v1/loans/{loan_id}/audit` | Customer Offer | Retrieve | the decision audit trail (ADR-0013) |
| loan-api | system | `POST /v1/loans/{loan_id}/notify` | Customer Offer | Notify |  |
| loan-api | system | `POST /v1/loans/{loan_id}/decision` | Customer Offer | Evaluate | the human approver's determination, recorded against the offer |
| process-api | process | `GET /v1/customers/by-account/{account_id}/overview` | **scenario: Customer Overview** spanning Current Account, Position Keeping, Customer Offer, Customer Behavior Insights | Retrieve | a BIAN business scenario, not a service domain — composition + next_action live here by ADR-0030 |
| process-api | process | `POST /v1/analyst/route` | *(outside the landscape)* | — | platform machinery — routes an analyst question to a capability; no banking counterpart |
<!-- <<< end generated -->
