---
type: Quality
title: Data Quality Rules & Service Levels
description: What "good" means per concept, and where the actual scan results live.
reviewed: 2026-07-27
review_by: 2026-10-27
owner: data-platform@datadinosaur.com
steward: data-steward@datadinosaur.com
---

# Data Quality Rules & Service Levels

An agent answering from data nobody monitors is a confident guess with extra steps. This
states the bar; the catalog holds the live results.

## Freshness

| Concept | Target | Measured by |
|---|---|---|
| [`transaction`](../tables/transaction.md) | < 5 min end-to-end (stream) | Pub/Sub → Dataflow → silver, event-time lag |
| [`account_balance`](../views/account-balance.md) | Derived — inherits transaction freshness | View, no independent lag |
| [`overdraft_history`](../views/overdraft-history.md) | Daily | Batch refresh |
| [`customer_360`](../views/customer-360.md) | Derived — recomputed per query | View |
| Knowledge-base corpus | Re-indexed on publish | Ingest run |

## Rules that must hold

| Rule | Concept | Severity |
|---|---|---|
| `transaction_id` unique (stream-deduped by idempotency key) | transaction | **blocking** |
| `account_id` in transaction resolves to an account | transaction | **blocking** |
| `customer_id` in account resolves to a customer | account | **blocking** |
| `txn_type` within the declared code set | transaction | warn |
| `amount` non-null for `POSTED` rows *(privileged read — masked reads legitimately see NULL)* | transaction | warn |
| `segment` populated | customer | warn |

Blocking rules are the ones that would silently corrupt a joined answer — a transaction
whose account does not resolve simply disappears from a customer-level aggregate.

## Where the truth lives

Rules are **executed** by the platform's profile and data-quality scans and surfaced in the
catalog, not in this file. This document is the *statement of intent*; the scan is the
*evidence*. If they disagree, the scan wins and this file is the bug.

## What an agent should do with this

- If a concept is failing a **blocking** rule, do not answer from it — say the data is
  currently failing quality checks and name the steward.
- Never present a **warn**-level gap as if the data were complete; state the caveat.
- Do not confuse a masked `NULL` with a completeness failure — see
  [known limitations](../limitations.md).

## Honest gap

Agents do **not** currently read live scan status at query time; a failing scan will not
automatically suppress an answer. That wiring — quality status as a retrieval filter — is
the natural next increment.
