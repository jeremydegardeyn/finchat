# 18 — Durable Agent Harness (Autonomous Reconciliation Steward)

> **Increment 19.** A long-running, durable agent that owns a crash-resumable
> reasoning loop — planner → generator → evaluator — that **sleeps at zero cost**
> and **wakes on the approver / events** with no polling. Decision record:
> [ADR-0021](adr/0021-durable-agent-harness.md). Code: `products/steward/`.

## 1. Why

The Loan Approval product (Increment 4) is a **fixed** orchestration: a developer-
defined Cloud Workflows route (Planner→Credit→TxnReview→Approval→Notification) with a
single human-in-the-loop callback, where long-running state lives in the *Workflows
execution* + BigQuery. That is correct for a regulated, auditable approval path.

It is the wrong shape for an **open-ended, long-running agent** — one that plans its
own work at runtime, self-corrects, runs for hours or days, sleeps at zero cost, and
wakes on events. Increment 19 adds that **general durable-agent engine** and uses it to
ship a new data product: an **Autonomous Reconciliation / Data-Quality Steward**.

The loan workflow is, conceptually, a *special case* of this engine (a fixed plan with
one gate). We keep both — the rigid route where the path must be fixed, the durable
agent where the agent must decide.

## 2. What the steward does

Each night (Cloud Scheduler) the steward:

1. **Plans** which data-quality / reconciliation checks to run against the active
   Dataplex **data contracts** (`contracts/*.yaml`, [docs/16](16-contracts.md)) and the
   Medallion gold tables.
2. **Generates** each check (a BigQuery / DaaS call via an ADK tool), one durable step
   at a time.
3. **Evaluates** each result against the contract using the **live-eval LLM-judge**
   ([ADR-0015](adr/0015-live-evaluation.md)), inline as a gate.
4. On low confidence → **escalates** to the **verified GIS approver**
   ([ADR-0016](adr/0016-identity-resolved-personas.md)) via a durable signal, and
   **sleeps** (zero cost) until the human responds — or a timeout auto-defers.
5. **Sleeps** between checks / until the next window, then resumes.
6. Writes an **append-only audit** row per step decision (same pattern as
   `approval_decision`) and publishes status as a durable event for the Admin UI.

Everything except the harness layer already exists in FinChat.

## 3. What's actually new vs. the loan workflow

| Concern | Loan workflow (Inc 4) | Durable harness (Inc 19) |
|---|---|---|
| Route | Fixed Cloud Workflows YAML | Plan generated at runtime; harness may **replan** |
| Quality gate | One final human approval | **Evaluator after every step** (self-correction) |
| State | Workflows execution + BQ table | Agent **working memory**, checkpointed, **resumable mid-loop** after a crash |
| Sleep | Incidental (await human) | **First-class** durable sleep for hours/days at zero cost |
| Wake | One callback | Any signal — human, timer, or Pub/Sub event |
| Locus of control | Orchestrator drives stateless agents | **Agent owns** a durable loop |

Net: dynamic planning, per-step evaluation, arbitrary durable sleep, event-driven
resume, and crash-resumable memory — added as *functionality*, not just extra steps.

## 4. Architecture — dual tier (deploy cheap, document enterprise 1:1)

Consistent with FinChat's serverless-substitution strategy ([ADR-0002](adr/0002-serverless-substitution-strategy.md)).

### 4a. Deploy tier — DBOS on Cloud Run
- **Engine:** DBOS (durable-execution *library*, not a server). The agent loop is
  ordinary Python; `@DBOS.step` checkpoints, `DBOS.sleep`, `DBOS.recv/send`,
  `DBOS.set_event` provide sleep / signals / status.
- **Durable store ("autosave"):** **Cloud SQL for PostgreSQL** (smallest tier). The only
  new always-on resource, gated behind `enable_agent_harness` (default **off**, like
  `enable_bigtable`).
- **Compute:** the steward runs on **Cloud Run** scale-to-zero ([ADR-0010](adr/0010-agents-on-cloud-run.md)).
  During `DBOS.sleep`/`recv` the instance can be evicted; on the next wake the workflow
  recovers from Postgres.
- **Secrets & connectivity (turnkey):** the DB password is generated (`random_password`)
  and stored in **Secret Manager** — never in tfvars/code; Cloud Run reads it as
  `DBOS_DATABASE_URL` and connects via the **Cloud SQL connector** (unix socket), so no
  public IP appears in the connection string and Cloud Run egress IPs need no
  allow-listing. TF owns the SQL + secret + Cloud Run **shell**; CI/CD owns the image +
  env (`ignore_changes`), exactly like the other services. **Deploy = set
  `enable_agent_harness=true` → `terraform apply` → one gated `build-deploy` run** (no
  password handling, no env-clobber). Enterprise hardening: private IP + Direct VPC egress.
- **Wake triggers (all push, no polling):** Cloud Scheduler (nightly), Pub/Sub→Eventarc
  (a new gold partition lands), and the BFF approver review → `DBOS.send` (generalizes
  the loan callback).
- **Reuse:** ADK tools, the live-eval judge, Dataplex contracts, GIS approver identity +
  server-side enforcement, append-only audit, Admin eval card.

### 4b. Enterprise tier — Temporal (documented 1:1)
The same loop as a Temporal Workflow (activities = LLM/tool calls, `workflow.sleep`,
signals for human input) on Temporal Cloud or self-hosted. We deploy DBOS, document
Temporal — the recognizable Fortune-500 reference. See [ADR-0021](adr/0021-durable-agent-harness.md).

## 5. State & memory model

- **Working memory** = checkpointed workflow state `{goal, plan, step_idx, history[],
  status}`, owned by the engine; survives crash / scale-to-zero.
- **Long-term memory** = the existing BigQuery `VECTOR_SEARCH` RAG ([ADR-0009](adr/0009-bigquery-vector-rag.md)).
- **Audit** = append-only `steward_decision` (`products/steward/schemas/ddl.sql`), same
  versioned, INSERT-only pattern as `approval_decision`.
- **Status** = durable event (`set_event`) read by the Admin UI — a projection, never
  the agent itself.

## 6. Governance / security

- **No lost work / no double side-effects** on crash (durable execution + idempotent
  steps).
- **Human authority** preserved: escalations require the **verified** approver email
  ([ADR-0016](adr/0016-identity-resolved-personas.md)); the BFF sets `approver` from the
  validated token, never client input, and writes it to the audit trail.
- **Zero idle cost** while sleeping/awaiting.
- **Model Armor** ([ADR-0008](adr/0008-model-armor-llm-screening.md)) still fronts LLM
  tool calls.

## 7. Verification (proven)

- **Offline** unit tests for planner/generator/evaluator (no Postgres / no key),
  `products/steward/harness/test_offline.py` — CI gate, same style as the loan tests.
- **Durable** behavior — sleep, per-step gating, human wake, and **crash recovery
  across a hard process kill** (DBOS `Recovering N workflows` on restart, completed
  steps not re-run) — demonstrated end-to-end on Postgres (see harness README).

## 8. Cost

| Item | Deploy tier | Note |
|---|---|---|
| Postgres (autosave) | Cloud SQL `db-f1-micro` | ~$10/mo 24/7; **~$3/mo with nightly stop/start** (Option B, `enable_scheduled_stop`); **$0 when the module toggle is off** (instance doesn't exist) |
| Compute | Cloud Run scale-to-zero | ~zero while asleep |
| Scheduler / Eventarc | negligible | push-based wake; Option B adds start/stop jobs |
| Enterprise 1:1 | Temporal Cloud | documented, not deployed |

**Cost note (Cloud SQL has no scale-to-zero):** the steward is nightly, so the module
can **start the instance before the run and stop it after** (`enable_scheduled_stop`,
default on; Cloud SQL Admin API `activationPolicy` ALWAYS/NEVER via two Cloud Scheduler
jobs). A *stopped* instance still bills storage + backups (~$2–4/mo) — only a
`terraform destroy` (toggle off) is truly $0. Because a stopped DB can't receive a wake,
scheduled mode keeps `human_wait_seconds` inside the on-window so escalations
**auto-defer** to the next run rather than holding the instance open.

## 9. CIO talking points

1. **Reliability** — work in flight is never lost and side-effects never double-fire,
   even if a server dies mid-process (demonstrated: killed mid-run, resumed from the
   last checkpoint).
2. **Cost** — agents cost ~nothing while waiting or sleeping (scale to zero).
3. **Governance** — every step is auditable; humans and events resume the agent
   instantly, no polling, no idle spend.
4. **Pragmatism** — we run the cheap, code-first engine (DBOS); the Fortune-500
   equivalent (Temporal) is the same design — documented 1:1.

## 10. Open questions

- Cloud SQL vs. a serverless Postgres (Neon/AlloyDB) under the zero-cost rule?
  (Decided for the sandbox: in-project Cloud SQL + nightly stop/start, so audit data
  never leaves the project — Neon's true-$0 idle isn't worth breaking the governance story.)
- One steward workflow per data product, or one fan-out with child runs?
- Scheduled-stop mode auto-defers escalations to the next window; revisit if the steward
  ever needs to hold an escalation open for same-day human review (would require 24/7 DB).
