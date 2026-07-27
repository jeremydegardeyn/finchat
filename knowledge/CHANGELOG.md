# Knowledge Bundle Changelog

Grounding changes are behaviour changes. This is the audit trail for them: what changed,
why, and whether it altered what an agent will say. Git history is the detail; this is the
reviewable summary.

Bundle version is declared in [`index.md`](index.md).

---

## 0.3.0 — 2026-07-27 (Inc 22: enterprise layers)

**Added — accountability, trust, control and agent-safety layers.**

- `glossary/` — business glossary with synonyms, owners and review dates. `active-customer`,
  `net-revenue`, `posted-transaction`, `overdraft-event` (certified) and `household`
  (**proposed, not modelled**).
- `playbooks/refusal-escalation.md` — 6 machine-readable refusal categories + escalation
  triggers. Compiled into agent system instructions.
- `golden-queries.yaml` — 13 vetted question→behaviour pairs. Grounding, eval set and
  acceptance criteria in one artifact.
- `limitations.md` — negative scope: what this bundle cannot answer.
- `stewardship.md` — role model, certification tiers, escalation path.
- `policies/data-handling.md` — retention, residency, purpose limitation, acceptable AI use.
- `compliance/regulatory-map.md` — concept → obligation → evidence (BCBS 239, SR 11-7,
  GLBA, CCPA, Reg E/DD, FFIEC).
- `quality/slos.md` — freshness targets and blocking/warn quality rules.
- `lineage.md` — end-to-end flow incl. the loans↔deposits cross-product dependency.
- `reference/code-sets.md` — **generated** from the ontology's enums.

**Behaviour change:** agents now carry explicit refusal rules and glossary synonyms.
Expect refusals on unmodelled concepts (household), identity requests, and advice — these
are intended.

## 0.2.0 — 2026-07-12 (Inc 20/21: ontology as SSOT)

- `ontology.yaml` becomes the conceptual source of truth; perimeter, join model and the
  `kg_relationships` view are all generated from it and CI drift-guarded.
- Reconciled a live drift: the join model had 3 relationships in the agent grounding and 4
  in the database view.
- Column classifications now **reference** the sensitivity taxonomy rather than
  re-describing it; assignments are checked against the deployed policy tags.
- Playbook frontmatter neutralised — those files are human documentation now.

## 0.1.0 — 2026-06-25 (initial bundle)

- Concept docs for datasets, tables, views, metrics and the property graph.
- Analyst perimeter and join paths as the first machine-readable grounding.
- Compiled to a committed, dependency-free Python module consumed by the analyst router.

---

## Change policy

| Change | Requires |
|---|---|
| New concept, or clarifying prose | Pull request + owner review |
| Metric definition, join model, or perimeter | Owner **and** steward approval — this changes answers |
| Refusal rule or policy | AI governance approval |
| Anything marked `certified` | Owner sign-off; bump the minor version |

Breaking changes — a removed concept, a redefined metric, a narrowed perimeter — get a
minor version bump and a note here explaining what answers change.
