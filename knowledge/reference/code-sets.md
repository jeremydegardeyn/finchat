---
type: Reference
title: Reference Code Sets
description: The permitted values for every enumerated field, generated from the ontology.
reviewed: 2026-07-27
review_by: 2027-01-27
owner: data-platform@datadinosaur.com
---

# Reference Code Sets

The permitted values for each enumerated field. An agent that does not know the code set
either invents a value (`type = 'TRANSFER_OUT'`) or silently returns nothing.

**This section is generated** from the `values:` declared on ontology properties, so the
codes an agent is told about cannot drift from the codes the model declares. Regenerate
with `python scripts/compile_okf.py`.

<!-- >>> generated from knowledge/ontology.yaml (scripts/compile_ontology.py) — do not edit by hand -->
| Concept | Field | Permitted values |
|---|---|---|
| Customer | `segment` | `retail` · `premium` · `business` |
| Account | `account_type` | `checking` · `savings` |
| Transaction | `txn_type` | `DEPOSIT` · `WITHDRAWAL` · `FEE` |
| Loan | `status` | `requested` · `in_review` · `approved` · `denied` |
<!-- <<< end generated -->

## Notes that are not derivable

- **`status` on a transaction is not enumerated in the ontology** — it is
  `POSTED` / `PENDING` / `REJECTED`, and only `POSTED` counts toward balances. See
  [Posted Transaction](../glossary/posted-transaction.md).
- **Loan workflow states in the physical table are richer** than the four above
  (`CREATED`, `PROFILED`, `REVIEWED`, `RECOMMENDED`, `PENDING_APPROVAL`, `APPROVED`,
  `REJECTED`, `MODIFIED`). The ontology models the four *business-meaningful* outcomes;
  the rest are workflow mechanics. Do not present workflow states as business status.
- **Codes are case-sensitive as written.** Transaction types are upper-case; segments and
  account types are lower-case. This asymmetry is real and a common source of empty results.
