---
type: Compliance
title: Regulatory Map
description: Which obligation applies to which concept, and where the evidence lives. The mapping an examiner asks for.
reviewed: 2026-07-27
review_by: 2027-01-27
owner: compliance@datadinosaur.com
steward: data-steward@datadinosaur.com
---

# Regulatory Map

Concept → obligation → evidence. The point is not to restate regulation; it is to make the
question "show me where this is handled" answerable in one hop.

## Obligations in scope

| Obligation | What it demands here | Where it is satisfied | Evidence |
|---|---|---|---|
| **BCBS 239** (risk data aggregation) | Accuracy, completeness and **traceable lineage** for risk data | Loan risk consumes `overdraft_history` as documented cross-product lineage | [lineage](../lineage.md), data contracts, DQ scans |
| **SR 11-7** (model risk management) | Inventory, documented purpose, validation and monitoring for every model | Model inventory + the eval harness scoring every conversation | `docs/19-model-inventory.md`, eval datasets, `conversation_log` |
| **GLBA** (safeguards / privacy) | Protect NPI; limit access to what is needed | Policy tags + column-level security + the analyst perimeter | [perimeter](playbooks/analyst-perimeter.md), classification refs in [`ontology.yaml`](../ontology.yaml) |
| **CCPA / state privacy** | Know what personal data exists, where, and delete on request | PII classified at column level; single deletion path via silver | [data handling](../policies/data-handling.md) |
| **Reg E / Reg DD** (disputes, disclosures) | Accurate disclosure of terms and fees | Product/fee documents answered from the knowledge base, never computed | Knowledge-base corpus |
| **FFIEC / GenAI guidance** | Human accountability for consequential decisions | Human-in-the-loop approval; agents are read-only | [data handling](../policies/data-handling.md), append-only decision audit |

## The AI-specific obligations people miss

**SR 11-7 applies to the agent, not just the credit model.** An LLM that shapes an
analyst's or a banker's conclusion is a model in the supervisory sense. What that demands
in practice, and how this platform answers it:

| Demand | How it is met |
|---|---|
| Documented purpose and limits | This bundle — especially [known limitations](../limitations.md) |
| Known inputs | The perimeter and injected grounding are declared, versioned artifacts |
| Ongoing performance monitoring | Every turn logged and LLM-judge scored; regressions are defects |
| Change control | Grounding changes arrive as a pull request with review and CI drift guards |
| Human accountability | Named owners and stewards per concept ([stewardship](../stewardship.md)) |

That last column is the real argument for a curated knowledge layer in a regulated bank:
**a prompt buried in application code satisfies none of it; a versioned, owned, CI-checked
artifact satisfies most of it as a side effect.**

## Honest gaps

- Obligation mapping is **maintained by hand** — it is not derived from anything, so it is
  the most drift-prone file in the bundle. Review date above is enforced by process, not code.
- No automated control testing. This map says where a control lives; it does not prove the
  control ran.
