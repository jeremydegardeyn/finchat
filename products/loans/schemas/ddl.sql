-- =============================================================================
-- FinChat — Loan Approval DDL (Data Product 2)
-- Append-only decision + audit tables for full auditability/versioning.
-- Replace ${PROJECT} / ${ENV} before running. Model: docs/data-model.md
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS `${PROJECT}.finchat_loans_${ENV}`
  OPTIONS (description = 'Loan approval data product: requests, profiles, risk, decisions, audit.');

-- ---------- Loan request ----------------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_loans_${ENV}.loan_request`
(
  loan_id        STRING NOT NULL,                 -- PK (UUID)
  customer_name  STRING NOT NULL,                 -- PII_DIRECT
  account_id     STRING,                          -- link to transactions product (nullable)
  amount         NUMERIC NOT NULL,                -- PII_FINANCIAL
  term_months    INT64 NOT NULL,
  status         STRING NOT NULL,                 -- CREATED|PROFILED|REVIEWED|RECOMMENDED|PENDING_APPROVAL|APPROVED|REJECTED|MODIFIED
  submitted_at   TIMESTAMP NOT NULL,
  updated_at     TIMESTAMP NOT NULL
)
PARTITION BY DATE(submitted_at)
CLUSTER BY status, customer_name;

-- ---------- Synthetic credit profile ----------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_loans_${ENV}.credit_profile`
(
  profile_id        STRING NOT NULL,              -- PK
  loan_id           STRING NOT NULL,              -- FK -> loan_request (NK)
  credit_score      INT64 NOT NULL,               -- CONFIDENTIAL (300-850)
  annual_income     NUMERIC,                      -- CONFIDENTIAL
  existing_debt     NUMERIC,
  dti_ratio         FLOAT64,                       -- debt-to-income
  generated_at      TIMESTAMP NOT NULL
)
PARTITION BY DATE(generated_at)
CLUSTER BY loan_id;

-- ---------- Risk assessment (versioned) -------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_loans_${ENV}.risk_assessment`
(
  assessment_id     STRING NOT NULL,              -- PK
  loan_id           STRING NOT NULL,              -- FK
  version           INT64 NOT NULL,               -- NK (loan_id + version)
  risk_score        INT64 NOT NULL,               -- 0(best)-100(worst)
  recommendation    STRING NOT NULL,             -- APPROVE|REVIEW|DECLINE
  overdraft_events  INT64,
  reasons           STRING,                       -- JSON array of factor strings
  model_version     STRING,
  created_at        TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY loan_id;

-- ---------- Approval decision (APPEND-ONLY, versioned) ----------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_loans_${ENV}.approval_decision`
(
  decision_id        STRING NOT NULL,             -- PK
  loan_id            STRING NOT NULL,             -- FK
  version            INT64 NOT NULL,              -- NK (loan_id + version) -> full history
  decision           STRING NOT NULL,            -- APPROVE|REJECT|REQUEST_MODIFICATION|COUNTEROFFER
  counteroffer_amount NUMERIC,
  approver           STRING NOT NULL,             -- authenticated approver identity
  rationale          STRING,
  decided_at         TIMESTAMP NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY loan_id, decision;
-- NOTE: writes are INSERT-only. No UPDATE/DELETE -> immutable, reconstructable history.

-- ---------- Audit log (immutable) -------------------------------------------
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_loans_${ENV}.loan_audit_log`
(
  audit_id     STRING NOT NULL,
  loan_id      STRING,
  actor        STRING NOT NULL,                   -- user / agent / workflow step
  action       STRING NOT NULL,
  detail       STRING,                            -- JSON
  event_time   TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_time)
CLUSTER BY loan_id, actor;

-- ---------- Serving view: current loan state --------------------------------
CREATE OR REPLACE VIEW `${PROJECT}.finchat_loans_${ENV}.loan_status` AS
WITH latest_decision AS (
  SELECT loan_id, decision, counteroffer_amount, approver, decided_at,
         ROW_NUMBER() OVER (PARTITION BY loan_id ORDER BY version DESC) AS rn
  FROM `${PROJECT}.finchat_loans_${ENV}.approval_decision`
),
latest_risk AS (
  SELECT loan_id, risk_score, recommendation,
         ROW_NUMBER() OVER (PARTITION BY loan_id ORDER BY version DESC) AS rn
  FROM `${PROJECT}.finchat_loans_${ENV}.risk_assessment`
)
SELECT
  r.loan_id, r.customer_name, r.amount, r.term_months, r.status,
  r.submitted_at, r.updated_at,
  lr.risk_score, lr.recommendation,
  ld.decision AS final_decision, ld.counteroffer_amount, ld.approver, ld.decided_at
FROM `${PROJECT}.finchat_loans_${ENV}.loan_request` r
LEFT JOIN latest_risk     lr ON r.loan_id = lr.loan_id AND lr.rn = 1
LEFT JOIN latest_decision ld ON r.loan_id = ld.loan_id AND ld.rn = 1;
