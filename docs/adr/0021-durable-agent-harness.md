# ADR-0021 — Durable-execution harness for long-running agents (DBOS deploy, Temporal documented)

- **Status:** Accepted (built + verified offline and on Postgres; deploy toggle default **off**)
- **Date:** 2026-06-19
- **Deciders:** Principal Cloud Architect
- **Context tags:** Long-running agents, durable execution, state management, dual-tier cost

## Context

FinChat needs a **general engine for long-running, open-ended agents** that plan their
own work at runtime, self-correct, run for hours/days, sleep at zero cost, and wake on
events. The existing Cloud Workflows + HITL loan pattern (Increment 4) is a *fixed*
orchestration — correct for a regulated approval route, but unsuited to a reasoning
loop whose plan is data and changes at runtime. This is the "multi-agent state
management" item flagged under future ADRs.

Requirements: durable / crash-resilient state (no lost work, no double side-effects);
true suspend-and-resume ("sleep") at zero idle cost on scale-to-zero compute; event /
human-driven wake with **no polling**; full auditability; and FinChat's near-zero
sandbox-cost discipline.

## Options considered

1. **Cloud Workflows + Cloud Tasks + Firestore (GCP-native).** Zero new infra; reuses
   existing patterns. But a *reasoning* loop in YAML is awkward, the plan can't easily
   be runtime data, and replanning is painful. Good for the fixed loan flow; poor for an
   agent.
2. **DBOS (durable-execution library + Postgres).** Code-first; durability, sleep,
   signals, and status are language primitives. Only new always-on resource is a small
   Postgres. Lowest ops burden and learning curve. **Chosen for deploy.**
3. **Restate (single durable server).** Excellent ergonomics (awakeables generalize the
   HITL callback) but adds a service to run. Viable alternative to DBOS.
4. **Temporal (full platform).** Industry standard, recognizable to a Fortune-500
   board; higher ops/cost (cluster or Temporal Cloud). **Chosen as the documented
   enterprise 1:1 tier.**

## Decision

Adopt a **durable-execution harness** for long-running agents:

- **Deploy tier:** **DBOS** on **Cloud Run** (scale-to-zero, ADR-0010) backed by **Cloud
  SQL Postgres**, gated behind `enable_agent_harness` (default **off**). Wakes via Cloud
  Scheduler (timer), Eventarc/Pub-Sub (event), and BFF `DBOS.send` (human) — all push.
- **Enterprise tier:** **Temporal**, documented 1:1, not deployed.
- First product on the harness = the **Autonomous Reconciliation / Data-Quality
  Steward** (`products/steward/`, [docs/18](../18-durable-agent-harness.md)), reusing
  ADK tools, the live-eval judge (ADR-0015) as the inline evaluator, Dataplex contracts,
  GIS approver identity + server-side enforcement (ADR-0016), and append-only audit.
- The **loan workflow stays on Cloud Workflows** (ADR-0005) — it is the fixed-route
  special case; we do not migrate it.

This mirrors prior dual-tier decisions (Apigee→API Gateway ADR-0006, Composer→Workflows
ADR-0005, Bigtable default-off ADR-0017): deploy the cheap, fit-for-purpose option;
document the enterprise equivalent.

## Consequences

**Positive**
- No lost work / no double side-effects on crash — verified by hard-killing the process
  mid-run; the workflow resumed from its last checkpoint (`Recovering N workflows`).
- Zero idle cost while sleeping/awaiting; agents scale to zero.
- Event/human-driven resumption with no polling; full per-step audit.
- A code-first loop is far more maintainable than a reasoning loop in YAML.

**Negative / costs**
- Introduces one new always-on resource (small Cloud SQL Postgres) when the toggle is
  on. Cloud SQL has no scale-to-zero — hence default-off and local-Postgres dev.
- A second execution model alongside Cloud Workflows (mitigated by a clear split: fixed
  routes on Workflows, open-ended agents on the harness).
- DBOS→Temporal is a documented mapping, not an automated migration.

**Follow-ups**
- ~~Cloud SQL vs. serverless Postgres under the zero-cost rule~~ — decided: in-project
  Cloud SQL with **nightly stop/start** (`enable_scheduled_stop`, ~$3/mo, $0 when the
  toggle is off) keeps audit data in-project; escalations auto-defer to the next window
  so a stopped DB never strands a parked run.
- Per-product vs. fan-out workflow topology.
