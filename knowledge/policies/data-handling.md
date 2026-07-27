---
type: Policy
title: Data Handling & Acceptable AI Use
description: Retention, residency, purpose limitation, and the rules for AI use of this data.
reviewed: 2026-07-27
review_by: 2027-01-27
owner: ai-governance@datadinosaur.com
steward: data-steward@datadinosaur.com
---

# Data Handling & Acceptable AI Use

The control-layer rules that apply to every concept in this bundle. Stated here so an
agent, an engineer and an auditor read the same version.

## Retention

| Layer | Retention | Rationale |
|---|---|---|
| Bronze (raw events) | 13 months | Replay and audit window |
| Silver (canonical) | 7 years | Regulatory record retention |
| Gold / graph (derived views) | Not stored — recomputed | Views over silver; no independent copy |
| Conversation log | 13 months | AI quality evaluation and incident review |

Derived surfaces holding no independent copy is deliberate: it keeps one deletion path.

## Residency & movement

- All data remains in the platform's designated region. No cross-region replication.
- **No customer data is used to train or fine-tune any model.** Inference only, with
  context passed per request.
- Prompts and completions are logged for evaluation; they are subject to the same
  classification and masking as the underlying columns.

## Purpose limitation

Data in this bundle is for **customer service, analytics and credit decisioning** as
described in the data products. Notably out of purpose:

- Marketing list building from the analyst surface.
- Any inference about protected characteristics.
- Re-identification of masked or de-identified values by combining surfaces.

## Acceptable AI use

1. **Read-only.** No agent may execute an account action. See the `action` refusal rule.
2. **Grounded answers only.** If it is not in the retrieved context or the injected model,
   the agent says it does not know.
3. **Attributable.** Every privileged action is taken as the *verified* signed-in
   identity, never a shared service identity.
4. **Human in the loop for consequential decisions.** Loan approval requires a human
   approver; the agent recommends and explains, it does not decide.
5. **Logged and evaluated.** Every turn is captured and scored; quality regressions are
   treated as defects.

## Relationship to enforcement

This document states intent. Enforcement is IAM, column-level security, policy tags and
the perimeter. Where a rule here is mechanically checkable, it should become a test — see
[known limitations](../limitations.md) for what is currently only stated.
