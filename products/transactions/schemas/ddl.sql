-- =============================================================================
-- FinChat — Banking Transactions DDL (canonical SQL form)
-- Mirrors the Terraform-managed tables (infra/modules/bigquery) for reference,
-- manual bootstrap, and code review. Replace ${PROJECT} / ${ENV} before running.
-- Partitioning + clustering + retention rationale: docs/data-model.md
-- =============================================================================

-- ---------- BRONZE: raw, immutable landing ----------------------------------
CREATE SCHEMA IF NOT EXISTS `${PROJECT}.finchat_bronze_${ENV}`
  OPTIONS (description = 'Raw immutable landing (replay/audit source of truth).');

CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_bronze_${ENV}.transaction_event`
(
  subscription_name STRING,
  message_id        STRING,          -- Pub/Sub message id (natural key)
  publish_time      TIMESTAMP,
  data              STRING,          -- raw JSON transaction payload
  attributes        STRING
)
PARTITION BY DATE(publish_time)
CLUSTER BY subscription_name
OPTIONS (partition_expiration_days = 400);

-- ---------- SILVER: cleansed, conformed, de-identified ----------------------
CREATE SCHEMA IF NOT EXISTS `${PROJECT}.finchat_silver_${ENV}`
  OPTIONS (description = 'Cleansed, conformed, deduplicated, PII de-identified.');

CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_silver_${ENV}.customer`
(
  customer_id          STRING NOT NULL,
  customer_natural_key STRING NOT NULL,           -- govt-id hash (NK)
  full_name            STRING,                     -- PII_DIRECT (policy tag)
  email                STRING,                     -- PII_DIRECT (policy tag)
  segment              STRING,
  created_at           TIMESTAMP NOT NULL,
  ingest_time          TIMESTAMP NOT NULL,
  pipeline_version     STRING
)
PARTITION BY DATE(created_at)
CLUSTER BY segment, customer_id;

CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_silver_${ENV}.account`
(
  account_id       STRING NOT NULL,
  account_number   STRING NOT NULL,                -- PII_FINANCIAL (policy tag)
  customer_id      STRING NOT NULL,                -- FK -> customer
  account_type     STRING NOT NULL,
  currency         STRING NOT NULL,
  status           STRING NOT NULL,
  opened_at        TIMESTAMP NOT NULL,
  ingest_time      TIMESTAMP NOT NULL,
  pipeline_version STRING
)
PARTITION BY DATE(opened_at)
CLUSTER BY customer_id, account_type;

CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_silver_${ENV}.transaction`
(
  transaction_id       STRING NOT NULL,
  idempotency_key      STRING NOT NULL,            -- NK for MERGE dedup
  account_id           STRING NOT NULL,            -- FK -> account
  txn_type             STRING NOT NULL,            -- DEPOSIT|WITHDRAWAL|TRANSFER|FEE
  amount               NUMERIC NOT NULL,           -- PII_FINANCIAL (policy tag)
  currency             STRING NOT NULL,
  counterparty_account STRING,                     -- PII_FINANCIAL (policy tag)
  status               STRING NOT NULL,            -- POSTED|PENDING|REJECTED
  event_time           TIMESTAMP NOT NULL,
  ingest_time          TIMESTAMP NOT NULL,
  source_system        STRING,
  pipeline_version     STRING
)
PARTITION BY DATE(event_time)
CLUSTER BY account_id, txn_type;

-- ---------- GOLD: business serving views ------------------------------------
CREATE SCHEMA IF NOT EXISTS `${PROJECT}.finchat_gold_${ENV}`
  OPTIONS (description = 'Business aggregates & serving views for APIs/agents.');

-- Current balance per account (derived from posted transactions).
CREATE OR REPLACE VIEW `${PROJECT}.finchat_gold_${ENV}.account_balance` AS
SELECT
  a.account_id,
  a.customer_id,
  a.currency,
  SUM(CASE WHEN t.txn_type = 'DEPOSIT'                 THEN t.amount
           WHEN t.txn_type IN ('WITHDRAWAL','FEE')     THEN -t.amount
           WHEN t.txn_type = 'TRANSFER'                THEN -t.amount
           ELSE 0 END) AS balance,
  MAX(t.event_time) AS last_activity_at
FROM `${PROJECT}.finchat_silver_${ENV}.account` a
LEFT JOIN `${PROJECT}.finchat_silver_${ENV}.transaction` t
  ON a.account_id = t.account_id AND t.status = 'POSTED'
GROUP BY a.account_id, a.customer_id, a.currency;

-- Account summary (activity counts + net) — also defined in Terraform.
CREATE OR REPLACE VIEW `${PROJECT}.finchat_gold_${ENV}.account_summary` AS
SELECT
  a.account_id,
  a.customer_id,
  a.account_type,
  a.currency,
  a.status,
  COUNTIF(t.txn_type = 'DEPOSIT')    AS deposit_count,
  COUNTIF(t.txn_type = 'WITHDRAWAL') AS withdrawal_count,
  COUNTIF(t.txn_type = 'FEE')        AS fee_count,
  SUM(CASE WHEN t.txn_type = 'DEPOSIT' THEN t.amount ELSE 0 END)
    - SUM(CASE WHEN t.txn_type IN ('WITHDRAWAL','FEE') THEN t.amount ELSE 0 END) AS net_balance,
  MAX(t.event_time) AS last_activity_at
FROM `${PROJECT}.finchat_silver_${ENV}.account` a
LEFT JOIN `${PROJECT}.finchat_silver_${ENV}.transaction` t USING (account_id)
GROUP BY 1,2,3,4,5;

-- Overdraft history (feeds the loan product's risk evaluation — cross-product lineage).
CREATE OR REPLACE VIEW `${PROJECT}.finchat_gold_${ENV}.overdraft_history` AS
WITH running AS (
  SELECT
    account_id,
    event_time,
    SUM(CASE WHEN txn_type = 'DEPOSIT' THEN amount
             WHEN txn_type IN ('WITHDRAWAL','FEE','TRANSFER') THEN -amount
             ELSE 0 END)
      OVER (PARTITION BY account_id ORDER BY event_time) AS running_balance
  FROM `${PROJECT}.finchat_silver_${ENV}.transaction`
  WHERE status = 'POSTED'
)
SELECT
  account_id,
  COUNTIF(running_balance < 0)                          AS overdraft_events,
  MIN(running_balance)                                  AS lowest_balance,
  COUNTIF(running_balance < 0) / NULLIF(COUNT(*), 0)    AS overdraft_ratio
FROM running
GROUP BY account_id;
