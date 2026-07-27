---
type: Limitations
title: Known Limitations — Negative Scope
description: What this bundle and the agents grounded on it cannot answer. Stated deliberately so gaps are known rather than guessed at.
reviewed: 2026-07-27
review_by: 2026-10-27
owner: ai-governance@datadinosaur.com
---

# Known Limitations

Most knowledge bases describe only what they contain. The absence of a stated boundary is
what lets an agent confidently answer a question it has no business answering. This is the
negative scope, on purpose.

## Not modelled at all

| Concept | Why it matters | Owner |
|---|---|---|
| **Household** | Asked constantly for pricing; no entity, no agreed rule. See [glossary](glossary/household.md). | customer-product |
| **Channel** | We cannot say whether a transaction came from mobile, branch or ATM — not captured in the ledger. | deposits-product |
| **Interest & fee income** | "Revenue" questions about income (not cash flow) cannot be answered. See [Net Revenue](glossary/net-revenue.md). | finance |
| **Marketing attribution** | No campaign or contact history in scope. | marketing |

## Modelled but deliberately off the analyst surface

- **Direct identifiers** — names, emails, account numbers. Structurally absent from the
  `dim_*` / `fact_*` views, not merely filtered. See the [perimeter](playbooks/analyst-perimeter.md).
- **Counterparty account** — present in silver, absent from `fact_transaction`.
- **Raw bronze events** — replay/audit only, never an answering surface.

## Answerable, but with a caveat the agent must state

- **Masked values.** For a non-privileged reader, `amount` and balances return `NULL` by
  design. That is a policy outcome, not missing data — see the `masked_null` rule in
  [refusal & escalation](playbooks/refusal-escalation.md).
- **Lifetime vs windowed counts.** `customer_360` rollups are lifetime. Any "last 90 days"
  question must be computed from `fact_transaction`, not read off the rollup.
- **Pending transactions.** Excluded from balances. See [Posted Transaction](glossary/posted-transaction.md).

## How to use this file

If a question maps to anything above, the correct behaviour is to say so and name the
owner. A precise "we don't model that yet, here's who to ask" is a better answer than a
plausible number — and it is the answer that survives an audit.
