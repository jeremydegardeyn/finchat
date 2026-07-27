---
type: GlossaryTerm
term: Household
status: proposed
owner: customer-product@datadinosaur.com
steward: data-steward@datadinosaur.com
reviewed: 2026-07-27
review_by: 2026-10-27
synonyms: [family group, relationship group]
maps_to: []
modelled: false
---

# Household

A set of customers treated as one relationship for pricing and marketing — typically
customers sharing an address or a joint account.

## Status: proposed, NOT modelled

There is **no household entity in the data model today.** Address is not on the analyst
surface, and joint-account ownership is not represented. Retail and Wealth also do not
currently agree on the rule (Wealth includes adult children at different addresses;
Retail does not).

**An agent must not attempt to compute households.** Say the concept is not yet modelled
and point the user at the owner above. This entry exists so the gap is *stated* rather
than silently guessed at — see [known limitations](../limitations.md).
