---
type: GlossaryTerm
term: Overdraft Event
status: certified
owner: deposits-product@datadinosaur.com
steward: data-steward@datadinosaur.com
reviewed: 2026-07-27
review_by: 2027-01-27
synonyms: [overdraft, OD, negative balance event, NSF event]
maps_to: [overdraft_history, overdraft_ratio]
---

# Overdraft Event

A single point at which an account's **running balance goes below zero**, derived from
posted transactions ordered by `event_time`.

Counted in [`gold.overdraft_history`](../views/overdraft-history.md) as
`overdraft_events`, and used by the
[overdraft ratio](../metrics/overdraft-ratio.md) metric and by loan risk scoring.

## Not the same as an overdraft fee

A customer can incur one overdraft event and be charged multiple fee items, or be
protected and charged none. **Fee counts are product/policy data (knowledge base), not
ledger data** — do not answer a fee question from this metric.
