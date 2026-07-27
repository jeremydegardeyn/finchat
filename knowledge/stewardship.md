---
type: Stewardship
title: Ownership & Stewardship
description: Who owns each concept, who stewards it, and how to escalate. The accountability layer around the semantic model.
reviewed: 2026-07-27
review_by: 2027-01-27
---

# Ownership & Stewardship

A knowledge bundle with no named owner rots. This is the accountability layer: every
concept traces to a human who can settle a definition dispute.

## The model

| Role | Owns | Accountable for |
|---|---|---|
| **Domain / product owner** | The meaning of their concepts | Is this definition right? Do we accept this as canonical? |
| **Data steward** | Quality and classification | Is it accurate, complete, correctly classified? |
| **Platform / AI engineering** | The compile step, drift guards, agent wiring | Do the projections match the model? |
| **AI governance** | Refusal policy, golden queries, eval bar | Is the agent behaving inside policy? |
| **Architecture** | The format itself | One standard, so knowledge stays portable. |

## Where ownership is recorded

Ownership is declared **on the concept**, not in a spreadsheet:

- **Classes** — `owner:` / `steward:` / `tier:` in [`ontology.yaml`](ontology.yaml).
- **Glossary terms** — `owner:` / `steward:` frontmatter per term.
- **Physical assets** — the authoritative producer commitment lives in the
  [data contracts](../contracts/README.md) (`owner.product`, `owner.steward`). The bundle
  **references** those; it does not restate them.

## Certification tiers

`tier:` on each class is the trust signal both humans and agents can read:

| Tier | Meaning | Agent behaviour |
|---|---|---|
| `certified` | Owner-approved, contract-backed, DQ-monitored | Answer freely |
| `curated` | Modelled and reviewed, not formally certified | Answer, note it is not certified |
| `raw` | Exists, not governed | Never an answering surface |

## Escalation

1. **Definition dispute** → the concept's `owner`. Two areas disagreeing on a term is an
   ownership question, not a technical one.
2. **Accuracy or classification concern** → the `steward`.
3. **Agent behaved outside policy** → AI governance, with the conversation id (every turn
   is logged for exactly this).
4. **A projection disagrees with the model** → platform engineering. The CI drift guard
   should have caught it; if it did not, the guard itself is the bug.
