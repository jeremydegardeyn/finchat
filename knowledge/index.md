---
type: Bundle
title: FinChat Knowledge Bundle
description: The curated, governed context FinChat's AI agents need but a foundation model cannot know — semantics, accountability, trust, control and agent-safety layers.
resource: https://github.com/jeremydegardeyn/finchat
tags: [finchat, banking, data-ai, okf, ontology, governance]
version: 0.3.0
reviewed: 2026-07-27
review_by: 2027-01-27
owner: ai-governance@datadinosaur.com
---

# FinChat Knowledge Bundle

An [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
bundle: the organization-specific context an agent needs but a foundation model cannot
know. Authored alongside code, versioned in git, and **compiled** into the artifacts that
actually ground the agents — see [`docs/20-ontology.md`](../docs/20-ontology.md).

> **This bundle is a front door, not a second copy.** Data contracts, column-level lineage,
> quality-scan results and the model inventory live in the catalog, the warehouse and the
> repo. The bundle *references* them and adds what none of them hold: agent-facing meaning,
> scope, and safety rules. Anything duplicated here is a future drift incident.

## Start here

| If you want to know… | Read |
|---|---|
| What the entities are and how they join | [`ontology.yaml`](ontology.yaml) — the conceptual SSOT |
| What a business term officially means | [`glossary/`](glossary/active-customer.md) |
| What a metric is, canonically | [`metrics/`](metrics/overdraft-ratio.md) |
| What analytics is allowed to touch | [`playbooks/analyst-perimeter.md`](playbooks/analyst-perimeter.md) |
| What the agent must refuse | [`playbooks/refusal-escalation.md`](playbooks/refusal-escalation.md) |
| What this bundle *cannot* answer | [`limitations.md`](limitations.md) |
| Who to ask when a definition is disputed | [`stewardship.md`](stewardship.md) |

## The layers

**Semantic — what the data means**
- [`ontology.yaml`](ontology.yaml) — classes, relationships (the join model), metrics, axioms, classification refs.
- [`glossary/`](glossary/active-customer.md) — business terms + synonyms, with owners and review dates.
- [`datasets/`](datasets/transactions.md) · [`tables/`](tables/transaction.md) · [`views/`](views/customer-360.md) · [`metrics/`](metrics/net-transaction-amount.md) · [`graph/`](graph/banking-graph.md)
- [`reference/code-sets.md`](reference/code-sets.md) — permitted values, **generated** from the ontology.

**Accountability — who answers for it**
- [`stewardship.md`](stewardship.md) — roles, certification tiers, escalation.
- `owner` / `steward` / `tier` on every ontology class.
- Producer commitments: the [data contracts](../contracts/README.md) *(referenced, not restated)*.

**Trust — is it fit to answer from**
- [`quality/slos.md`](quality/slos.md) — freshness targets, blocking vs warn rules.
- [`lineage.md`](lineage.md) — end-to-end flow, incl. the loans↔deposits cross-product hop.
- [`limitations.md`](limitations.md) — negative scope, stated out loud.

**Control — what may be seen and done**
- [`playbooks/analyst-perimeter.md`](playbooks/analyst-perimeter.md) — the analytics allow-list.
- [`policies/data-handling.md`](policies/data-handling.md) — retention, residency, purpose, acceptable AI use.
- [`compliance/regulatory-map.md`](compliance/regulatory-map.md) — concept → obligation → evidence.

**Agent safety — the layer classic catalogs skip**
- [`playbooks/refusal-escalation.md`](playbooks/refusal-escalation.md) — refusal categories + escalation triggers.
- [`golden-queries.yaml`](golden-queries.yaml) — vetted question→behaviour pairs: grounding, eval set and acceptance bar in one.

**Lifecycle**
- [`CHANGELOG.md`](CHANGELOG.md) — what changed, why, and whether answers changed.
- `reviewed` / `review_by` frontmatter on every concept.

## How it reaches the agents

`python scripts/compile_okf.py` regenerates every projection:

| Compiled to | Feeds |
|---|---|
| `ANALYST_PERIMETER` | the Conversational Analytics table allow-list |
| `ANALYST_JOIN_BULLETS` | the CA system instruction's join model |
| `ANALYST_KNOWLEDGE` | the Data Model (semantics) route's grounding corpus |
| `ANALYST_GLOSSARY` | term/synonym resolution — and, next, retrieval query expansion |
| `ANALYST_REFUSAL_BULLETS` | refusal rules injected into agent system instructions |
| `kg_relationships` | the BigQuery join view that grounds generated SQL |
| `reference/code-sets.md` | the permitted-values table above |

CI drift guards (`scripts/test_ontology.py`, `ui/test_okf_context.py`) fail the build if any
committed projection stops matching this bundle.

## How agents should use it

1. Resolve the question's wording to a **glossary term** first; if the term is marked not
   modelled, decline and name the owner.
2. Check [`limitations.md`](limitations.md) before answering — a precise "we don't model
   that" beats a plausible number.
3. For analytics, ground only on the [perimeter](playbooks/analyst-perimeter.md) views and
   the ontology's join model. Never name a physical `silver`/`bronze` table.
4. For metrics, use the canonical [definitions](metrics/overdraft-ratio.md) — never re-derive.
5. Apply the [refusal rules](playbooks/refusal-escalation.md). They override helpfulness.

## What this bundle is not

It **grounds**; it does not **enforce**. IAM, column-level security and policy tags are the
controls. The bundle shapes what an agent attempts and says — both layers are required.
