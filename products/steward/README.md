# Data Product — Autonomous Reconciliation / Data-Quality Steward (Increment 19)

A **long-running, durable agent** that runs nightly to reconcile the gold tables and
flag data-quality anomalies against the active **Dataplex data contracts**, escalating
to the **verified approver** (Inc 15) only when its inline evaluator is unsure — then
**sleeping at zero cost** until the human (or an event) wakes it.

Where Product 2 (Loans) is a **fixed** Cloud Workflows route with one human gate, the
steward is the **general durable-agent engine**: the plan is generated at runtime and
the agent owns a crash-resumable reasoning loop. The loan flow is effectively a special
case of this engine.

## Layout

| Path | Purpose |
|---|---|
| `harness/` | The durable agent engine (DBOS) — planner/generator/evaluator + FastAPI |
| `schemas/ddl.sql` | Append-only `steward_run` / `steward_decision` / audit + serving view |

## What it reuses (only the harness layer is new)

- **ADK tools** → DaaS APIs + BigQuery data-quality / reconciliation checks
- **`live_eval` LLM-judge** (ADR-0015) → the inline per-step evaluator
- **Dataplex contracts** (`contracts/*.yaml`) → what each check is validated against
- **GIS approver identity** (ADR-0016) → server-side escalation authority + audit
- **Append-only audit** → same immutable pattern as `approval_decision`

## Design

- [docs/18-durable-agent-harness.md](../../docs/18-durable-agent-harness.md)
- [ADR-0021](../../docs/adr/0021-durable-agent-harness.md) — DBOS deploy / Temporal documented 1:1

## Infra

`infra/modules/agent_harness` — Cloud Run (scale-to-zero) + Cloud SQL Postgres +
Cloud Scheduler, behind `enable_agent_harness` (default **off**, like Bigtable).
