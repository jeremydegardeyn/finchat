# ADR-0005 — Cloud Workflows + Scheduler over Cloud Composer for orchestration

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** Orchestration, cost engineering, long-running workflows, HITL

## Context

The brief lists Cloud Composer (managed Apache Airflow) as a preferred service. We must orchestrate:
- **Product 1 ingestion** — event-driven streaming (Pub/Sub → Dataflow → BigQuery).
- **Product 2 loan approval** — a *long-running* workflow that **pauses for human approval** (hours/days).

Composer is always-on (GKE + Airflow web server + metadata DB), costing ~$300–500/mo even idle.

## Decision

**Do not deploy Composer.** Use **Cloud Workflows** for the loan orchestration and **Cloud Scheduler**
for any cron triggers. Document Composer as the **enterprise target for batch DAG orchestration** that
is *added later* when batch workloads exist — not as a replacement for Workflows.

## Rationale

- **Streaming needs no orchestrator.** Pub/Sub + Dataflow is the orchestration for Product 1.
- **Airflow is poorly suited to long HITL waits.** Parking a DAG for days awaiting an approver
  callback fights Airflow's scheduler model. Cloud Workflows has **native `callback` endpoints** that
  suspend execution until an authenticated HTTP callback arrives — purpose-built for human-in-the-loop.
- **Cost.** Workflows + Scheduler are serverless and effectively free at this volume
  (5k steps/mo + 3 jobs free); Composer bills 24/7.
- **Right tool, right job.** Composer/Airflow shines for **scheduled batch DAGs with complex
  dependencies** — nightly reconciliation, regulatory batch reports, dbt transformations, ML training
  schedules. The sandbox has none of these yet.

## When Composer *is* added (enterprise trigger)

Introduce Composer when the platform gains: nightly GL reconciliation, scheduled regulatory reporting
(e.g., batch extracts), orchestrated dbt model builds, or ML retraining pipelines with upstream data
dependencies. At that point Composer **complements** Workflows (batch vs. event/HITL), it does not
replace it.

## Consequences

- The loan workflow's long-running + HITL logic is implemented in Workflows YAML with `callback` +
  retry/error handling (see [`products/loans/workflow/`](../../products/loans/workflow/)).
- Migration to a Composer-inclusive topology is **additive** — no rework of existing flows.
- Saves ~$300–500/mo with a better fit for the actual workloads.
