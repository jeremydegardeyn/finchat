-- =============================================================================
-- FinChat — Durable Steward DDL (Increment 19 / ADR-0021)
-- Append-only run + decision + audit tables for the long-running reconciliation
-- agent. The DURABLE working state lives in Postgres (DBOS); BigQuery holds the
-- immutable business record for governance/audit.
-- Replace ${PROJECT} / ${ENV} before running. Model: docs/18-durable-agent-harness.md
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS `${PROJECT}.finchat_steward_${ENV}`
  OPTIONS (description = 'Durable reconciliation steward: runs, step decisions, audit.');

-- ---------- Steward run (one durable workflow) ------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_steward_${ENV}.steward_run`
(
  run_id        STRING NOT NULL,                 -- PK == DBOS workflow_id
  goal          STRING NOT NULL,
  status        STRING NOT NULL,                 -- PLANNED|WORKING|AWAITING_HUMAN|DONE|DEFERRED
  started_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP NOT NULL
)
PARTITION BY DATE(started_at)
CLUSTER BY status;

-- ---------- Step decision (APPEND-ONLY) -------------------------------------
-- One row per evaluated step. Escalations record the VERIFIED approver identity
-- (Inc 15) and resolution -> reconstructable history, no UPDATE/DELETE.
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_steward_${ENV}.steward_decision`
(
  decision_id   STRING NOT NULL,                 -- PK
  run_id        STRING NOT NULL,                 -- FK -> steward_run
  step_index    INT64 NOT NULL,                  -- NK (run_id + step_index)
  task          STRING NOT NULL,
  result        STRING,
  eval_score    FLOAT64 NOT NULL,                -- evaluator confidence 0..1
  eval_reason   STRING,
  resolution    STRING,                          -- auto|human_approved|human_rejected|human_revise|timeout_auto_defer
  approver      STRING,                          -- authenticated approver (escalations only)
  note          STRING,
  decided_at    TIMESTAMP NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY run_id, resolution;
-- NOTE: writes are INSERT-only -> immutable, auditable step history.

-- ---------- Audit log (immutable) -------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_steward_${ENV}.steward_audit_log`
(
  audit_id     STRING NOT NULL,
  run_id       STRING,
  actor        STRING NOT NULL,                  -- planner|generator|evaluator|approver|harness
  action       STRING NOT NULL,
  detail       STRING,                           -- JSON
  event_time   TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_time)
CLUSTER BY run_id, actor;

-- ---------- Serving view: latest state per run ------------------------------
CREATE OR REPLACE VIEW `${PROJECT}.finchat_steward_${ENV}.steward_status` AS
WITH latest_step AS (
  SELECT run_id, step_index, task, eval_score, resolution, approver, decided_at,
         ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY step_index DESC) AS rn
  FROM `${PROJECT}.finchat_steward_${ENV}.steward_decision`
)
SELECT
  r.run_id, r.goal, r.status, r.started_at, r.updated_at,
  ls.step_index AS last_step, ls.task AS last_task, ls.eval_score AS last_score,
  ls.resolution AS last_resolution, ls.approver AS last_approver
FROM `${PROJECT}.finchat_steward_${ENV}.steward_run` r
LEFT JOIN latest_step ls ON r.run_id = ls.run_id AND ls.rn = 1;
