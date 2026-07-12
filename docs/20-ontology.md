# 20 — Bank Ontology as the conceptual SSOT (Inc 20)

## Problem

The FinChat join model lived in **three** hand-kept places that drifted:

1. the OKF join bullets compiled into `ui/_okf_context.py` (had **3** relationships),
2. the `kg_relationships` BigQuery view in `products/graph/schemas/graph.sql` (had **4** — it also carried `customer_360 ROLLS_UP Customer`), and
3. the prose in the analyst playbooks.

The analyst semantic perimeter was a fourth hand-maintained artifact. Nothing forced these to agree, and (1) and (2) had already diverged.

## Solution

A single conceptual model — [`knowledge/ontology.yaml`](../knowledge/ontology.yaml) — is now the source of truth. It declares **classes** (entities bound to their curated views), **relationships** (object properties = the join model), **metrics** (derived properties), **axioms** (constraints), and per-class **governance** (perimeter role, PII/masking tags).

`scripts/compile_ontology.py` projects that one model into every downstream artifact:

| Projection | Artifact | Consumer |
|---|---|---|
| `perimeter(model)` | `ANALYST_PERIMETER` | the CA table allow-list (`_analyst_tables`) |
| `join_bullets(model)` | `ANALYST_JOIN_BULLETS` | the CA system instruction |
| `kg_select_sql(model)` | the `kg_relationships` view body in `graph.sql` | Conversational Analytics grounding |

`scripts/compile_okf.py` consumes the first two (alongside the descriptive concept corpus → `ANALYST_KNOWLEDGE`). One command, `python scripts/compile_okf.py`, regenerates all of it.

Inc 20 **reconciled the drift**: the OKF join bullets went from 3 → 4, matching the graph view (the `customer_360` join is accurate and the view is already in the perimeter).

## Drift guards

- `scripts/test_ontology.py` — the committed perimeter, join bullets, and the `graph.sql` `kg_relationships` region must equal a fresh projection of the ontology. Regenerating must be a no-op.
- `ui/test_okf_context.py` — the committed `_okf_context.py` must match a fresh compile.

Both run in CI (`.github/workflows/ci.yml`), so no projection can silently diverge from the ontology again.

## What this is — and is not

This is a **pragmatic YAML ontology**, deliberately not OWL/RDF with a reasoner. At FinChat's scale a triple store and automated inference are overkill; a YAML model plus a validator captures the classes, relationships, and constraints that actually drive the system.

At enterprise scale you would push further: express the ontology in OWL/SKOS for cross-domain interop, run a reasoner to *infer* relationships and check consistency, and generate not just grounding + graph DDL but also dbt relationship tests, Dataplex catalog aspects, and enforced (not merely documented) metrics. The architecture here — **one authored model, many generated projections, CI-guarded** — is the same; only the formality of the model and the number of projections grow.

Like OKF, the ontology **documents and grounds**; it does not **enforce**. IAM and column-level security remain the enforcement plane. The ontology now *generates* the perimeter and the PII/masking annotations that configure that policy — tightening the loop rather than replacing it.

## Classification taxonomy (Inc 21)

A **taxonomy** is a hierarchical classification — a controlled vocabulary. The column-level-security taxonomy (`google_data_catalog_taxonomy` "classification" with `PII_DIRECT`, `PII_FINANCIAL`, `CONFIDENTIAL`, in `infra/modules/bigquery/main.tf`) is exactly that, and it is a separate governed artifact: it is owned centrally and shared across products, so it stays in Terraform, not folded into the ontology.

The split of responsibility:

- The taxonomy owns the **term definitions** and their masking policy (what `PII_FINANCIAL` means and that it masks to NULL for the analyst tier).
- The ontology owns the **assignment** — which column carries which term — via `classification: PII_FINANCIAL` on the property. It *references* a taxonomy term; it does not re-describe it (the earlier ad-hoc `pii:`/`masking:` fields were a third copy and are gone).

Two drift guards in `scripts/test_ontology.py` make this a real single source of truth:

- every `classification:` the ontology references must be a defined term in the Terraform taxonomy, and
- the ontology's `(column, term)` assignments must equal the policy tags actually deployed on the silver columns.

So the ontology and the deployed column-level security can no longer disagree about what is sensitive. Note this is assignment-level: masking policy still lives in the taxonomy, and view-omission (a column absent from the analyst surface entirely, like `account_number`) is a separate decision from masking-in-place (a column present but nulled for the analyst tier, like `amount`) — the classification drives the latter, not the former.
