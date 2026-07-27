---
type: GlossaryTerm
term: Posted Transaction
status: certified
owner: deposits-product@datadinosaur.com
steward: data-steward@datadinosaur.com
reviewed: 2026-07-27
review_by: 2027-01-27
synonyms: [settled transaction, cleared transaction, posted item]
maps_to: [fact_transaction, account_balance]
---

# Posted Transaction

A transaction with `status = 'POSTED'` — it has settled and affects the balance.

**Only posted rows count toward balances and metrics.** `PENDING` and `REJECTED` rows
exist in [`fact_transaction`](../views/fact-transaction.md) and will silently inflate any
count or sum that does not filter them out. This is the single most common source of
"the numbers don't tie" in this platform.

| Status | Counts toward balance? |
|---|---|
| `POSTED` | Yes |
| `PENDING` | No — authorised, not settled |
| `REJECTED` | No |
