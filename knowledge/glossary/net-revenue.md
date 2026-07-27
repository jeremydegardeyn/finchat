---
type: GlossaryTerm
term: Net Revenue
status: certified
owner: finance@datadinosaur.com
steward: data-steward@datadinosaur.com
reviewed: 2026-07-27
review_by: 2027-01-27
synonyms: [revenue, net amount, net flow, net position]
maps_to: [net_transaction_amount, customer_360, account_balance]
---

# Net Revenue

In this platform, "revenue" in an analyst question almost always means the
[**net transaction amount**](../metrics/net-transaction-amount.md) — the signed sum of
posted transactions, where `DEPOSIT` is positive and `WITHDRAWAL` / `FEE` are negative.

This is **not** accounting revenue. It is a cash-flow measure over the transaction
ledger. If a question is really about fee income or interest income, say that those are
not on the analyst surface rather than substituting this metric.

## Why this term exists

Business users say "revenue"; the model is called `net_transaction_amount`. Without this
mapping an agent either fails to find anything or silently invents its own definition.
