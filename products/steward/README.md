# Data Product — Data-Quality Remediation Steward (Increment 19)

A **long-running, durable agent** that orchestrates **remediation-with-approval on top
of the existing Dataplex DQ datascans** (Inc 10). It does **not** re-implement data
quality — Dataplex Auto DQ already runs the checks. Each night the steward reads the DQ
scan results and, for every failed rule, proposes a remediation, **durably waits for the
verified approver** (Inc 15), and on approval records the order + re-runs the scan to
verify — **sleeping at zero cost** in between.

**Why an agent and not just scheduled SQL:** the checking *is* scheduled SQL (Dataplex).
What needs a durable agent is the **remediation workflow** — a branching, long-running,
human-in-the-loop, exactly-once process that scheduled SQL can't express. Governance
posture: the steward never directly rewrites production financial tables; it records an
approved remediation **order** and triggers re-validation — the owning team executes the
fix.

Where Product 2 (Loans) is a **fixed** Cloud Workflows route with one human gate, the
steward is the **general durable-agent engine**: the work list is built at runtime from
live findings and the agent owns a crash-resumable loop.

## Layout

| Path | Purpose |
|---|---|
| `harness/` | The durable agent engine (DBOS) — plan/propose/assess/apply + FastAPI |
| `schemas/ddl.sql` | Append-only `steward_run` / `steward_decision` / audit + serving view |

## What it reuses (only the harness layer is new)

- **Dataplex DQ datascans** (Inc 10) → the source of findings (read, not re-run as checks)
- **Gemini via Vertex AI** (ADR-0004) → proposes conservative remediation orders
- **GIS approver identity** (ADR-0016) → server-side approval authority + audit
- **Append-only audit** → same immutable pattern as `approval_decision`

## Design

- [docs/18-durable-agent-harness.md](../../docs/18-durable-agent-harness.md)
- [ADR-0021](../../docs/adr/0021-durable-agent-harness.md) — DBOS deploy / Temporal documented 1:1

## Infra

`infra/modules/agent_harness` — Cloud Run (scale-to-zero) + Cloud SQL Postgres +
Cloud Scheduler, behind `enable_agent_harness` (default **off**, like Bigtable).
