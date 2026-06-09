# ADR-0013 — Loan decision explainability (factor attribution + reason codes)

- **Status:** Accepted
- **Date:** 2026-06-09
- **Deciders:** Principal Data Architect
- **Context tags:** Explainability, model risk, lending compliance, governance

## Context

The loan risk model (`products/loans/api/risk.py`) is a transparent additive
scorecard over four factors (credit 0-40, DTI 0-30, overdraft 0-20, loan size 0-10).
It computed each factor's point contribution but kept only a flat prose `reasons`
list — so the *decision was explainable in principle but not surfaced*. Regulated
lending requires more: **ECOA / Reg B** adverse-action notices must state the
**principal reasons** for a denial, and **SR 11-7** expects model decisions to be
transparent, reproducible, and auditable.

## Decision

Make the scorecard's per-factor attribution a first-class, persisted output and
surface it to humans:

- **`score_risk` returns `factors[]`** — each `{code, label, value, points,
  max_points, note, impact}`. Points sum exactly to the risk score (checked in
  tests), so the score is fully decomposable.
- **`RiskResult.principal_reasons`** ranks the risk-increasing factors highest-first
  — the ECOA/Reg B "principal reasons" for a review/denial (empty when nothing
  increased risk).
- **Persisted + served:** a `factors` JSON column on `risk_assessment` (added via
  idempotent `ALTER ... ADD COLUMN IF NOT EXISTS`); the `loan_status` view exposes
  `reasons` + `factors`; the loan API returns `factors` + `principal_reasons` +
  `model_version` on submit and on status reads.
- **Surfaced in the UI:** a *"Why this decision"* scorecard (per-factor contribution
  + principal reasons) on the customer's loan result, and a *"Why"* panel in the
  Loan Officer review queue.

## Rationale

- **Glass-box over post-hoc:** the model is already additive, so exact factor
  attribution is intrinsic — no SHAP/Vertex Explainable AI approximation needed; the
  explanation *is* the model. (Post-hoc attribution stays the path if a black-box
  model is later introduced.)
- **Compliance-ready:** reason codes map directly to adverse-action requirements;
  every assessment is versioned and immutable in `risk_assessment` + `loan_audit_log`.
- **Reproducible:** deterministic profile + scorecard means the same loan always
  yields the same explanation (auditable).

## Consequences

- Existing rows have `factors = NULL` until re-scored; the UI falls back to prose
  `reasons`. The DDL change is additive (no rewrite, no downtime).
- The `reasons` prose list is retained for backward compatibility.
- Surfaces explainability for the **Customer** and **Loan Officer** personas; the
  **Analyst** Conversational Analytics panel already shows its generated SQL as its
  own form of transparency (ADR-0012).

## Alternatives considered

- **Vertex Explainable AI / SHAP feature attributions:** rejected for now — the model
  is a transparent scorecard; post-hoc attribution would add cost and approximation
  for no gain. Revisit if/when a learned model replaces the scorecard.
- **Leave reasons as prose only:** rejected — not machine-usable, can't rank principal
  reasons, doesn't satisfy adverse-action structure.
