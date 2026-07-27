---
type: GlossaryTerm
term: Active Customer
status: certified
owner: customer-product@datadinosaur.com
steward: data-steward@datadinosaur.com
reviewed: 2026-07-27
review_by: 2027-01-27
synonyms: [active client, engaged customer]
maps_to: [customer_360, fact_transaction]
---

# Active Customer

A customer with **at least one `POSTED` transaction in the trailing 90 days**.

This is the certified definition. Marketing has historically used a 30-day window and
Risk has used 12 months; both are *different metrics*, not different names for this one.
If a question implies a different window, say which window you used.

## How to compute it

Count distinct `customer_id` reachable from `fact_transaction` where
`status = 'POSTED'` and `event_time >= CURRENT_TIMESTAMP() - INTERVAL 90 DAY`, bridging
`fact_transaction → dim_account → dim_customer` (see
[join paths](../playbooks/analyst-join-paths.md)).

`customer_360.transaction_count` is a **lifetime** count, not a 90-day count — do not
substitute it for this term.
