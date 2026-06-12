-- =============================================================================
-- FinChat — Knowledge Graph (BigQuery semantic layer)
-- A native property graph (banking_graph, queried with GQL) over the operational
-- entities, the kg_relationships join schema that grounds Conversational Analytics,
-- and a denormalized customer_360 so CA has explicit, correct joins. Views only —
-- no data is copied. Replace ${PROJECT}/${ENV}.
--
-- Entity-relationship (the data model):
--   Customer (customer_id)
--     └─HAS_ACCOUNT→ Account (account_id, customer_id)
--                      ├─OCCURS_ON← Transaction (account_id)
--                      ├─SUMMARIZED_BY← OverdraftProfile (account_id)
--                      └─REQUESTED← Loan (account_id)
-- PII (full_name/email) is intentionally excluded — analytics needs ids + segments.
-- =============================================================================

-- ---------- NATIVE property graph (BigQuery Graph / GQL) ---------------------
-- A real property graph over the operational entities — queried with GQL
-- (GRAPH_TABLE ... MATCH), e.g.:
--   SELECT segment, COUNT(*) FROM GRAPH_TABLE(`${PROJECT}.finchat_graph_${ENV}.banking_graph`
--     MATCH (c:Customer)-[:OWNS]->(a:Account)<-[:ON_ACCOUNT]-(t:Transaction)
--     COLUMNS (c.segment AS segment)) GROUP BY segment;
-- Metadata-only over existing tables (no copies, no extra storage cost). The kg_*
-- VIEWS below remain the grounding for Conversational Analytics (CA emits SQL,
-- not GQL); the property graph serves native graph analytics (multi-hop paths,
-- relationship patterns — e.g. fraud-ring style traversals at enterprise).
CREATE OR REPLACE PROPERTY GRAPH `${PROJECT}.finchat_graph_${ENV}.banking_graph`
NODE TABLES (
  `${PROJECT}.finchat_silver_${ENV}.customer`    AS Customer    KEY (customer_id),
  `${PROJECT}.finchat_silver_${ENV}.account`     AS Account     KEY (account_id),
  `${PROJECT}.finchat_silver_${ENV}.transaction` AS Transaction KEY (transaction_id),
  `${PROJECT}.finchat_loans_${ENV}.loan_request` AS Loan        KEY (loan_id)
)
EDGE TABLES (
  `${PROJECT}.finchat_silver_${ENV}.account` AS OWNS
    KEY (account_id)
    SOURCE KEY (customer_id) REFERENCES Customer (customer_id)
    DESTINATION KEY (account_id) REFERENCES Account (account_id),
  `${PROJECT}.finchat_silver_${ENV}.transaction` AS ON_ACCOUNT
    KEY (transaction_id)
    SOURCE KEY (transaction_id) REFERENCES Transaction (transaction_id)
    DESTINATION KEY (account_id) REFERENCES Account (account_id),
  `${PROJECT}.finchat_loans_${ENV}.loan_request` AS REQUESTED
    KEY (loan_id)
    SOURCE KEY (account_id) REFERENCES Account (account_id)
    DESTINATION KEY (loan_id) REFERENCES Loan (loan_id)
);

-- ---------- Join relationships (the graph schema; feeds the CA system prompt) ----------
CREATE OR REPLACE VIEW `${PROJECT}.finchat_graph_${ENV}.kg_relationships` AS
-- NOTE: names reference the SEMANTIC views (dim_/fact_), never physical silver
-- tables — this content grounds the NL model, and teaching it silver names makes
-- it generate out-of-perimeter SQL that IAM then (correctly) denies.
SELECT * FROM UNNEST([
  STRUCT('dim_account'       AS from_table, 'customer_id' AS from_column,
         'dim_customer'      AS to_table,   'customer_id' AS to_column,
         'Account BELONGS_TO Customer'      AS relationship),
  STRUCT('fact_transaction', 'account_id', 'dim_account', 'account_id', 'Transaction OCCURS_ON Account'),
  STRUCT('overdraft_history', 'account_id', 'dim_account', 'account_id', 'OverdraftProfile SUMMARIZES Account'),
  STRUCT('customer_360', 'customer_id', 'dim_customer', 'customer_id', 'Customer360 ROLLS_UP Customer')
]);

-- The literal graph (entity instances + directed relationships) is now owned by
-- the NATIVE `banking_graph` property graph above (queried with GQL). The earlier
-- hand-rolled `kg_nodes` / `kg_edges` views were redundant scaffolding and have
-- been pruned. `kg_relationships` stays — it is the CA grounding (join schema as
-- data), a different job from graph traversal.

-- ---------- customer_360: denormalized per-customer rollup (CLS-safe) ----------
CREATE OR REPLACE VIEW `${PROJECT}.finchat_graph_${ENV}.customer_360` AS
WITH acct AS (
  SELECT customer_id, COUNT(*) AS account_count
  FROM `${PROJECT}.finchat_silver_${ENV}.account` GROUP BY customer_id
),
tx AS (
  SELECT a.customer_id,
         COUNT(*) AS transaction_count,
         SUM(CASE WHEN t.txn_type = 'DEPOSIT' THEN t.amount
                  WHEN t.txn_type IN ('WITHDRAWAL', 'FEE') THEN -t.amount ELSE 0 END) AS net_transaction_amount
  FROM `${PROJECT}.finchat_silver_${ENV}.transaction` t
  JOIN `${PROJECT}.finchat_silver_${ENV}.account` a USING (account_id)
  GROUP BY a.customer_id
),
od AS (
  SELECT a.customer_id,
         SUM(o.overdraft_events) AS overdraft_events,
         MIN(o.lowest_balance) AS lowest_balance
  FROM `${PROJECT}.finchat_gold_${ENV}.overdraft_history` o
  JOIN `${PROJECT}.finchat_silver_${ENV}.account` a USING (account_id)
  GROUP BY a.customer_id
),
ln AS (
  SELECT a.customer_id,
         COUNT(*) AS loan_count,
         SUM(l.amount) AS total_loan_amount
  FROM `${PROJECT}.finchat_loans_${ENV}.loan_request` l
  JOIN `${PROJECT}.finchat_silver_${ENV}.account` a USING (account_id)
  GROUP BY a.customer_id
)
SELECT
  c.customer_id,
  c.segment,
  c.created_at AS customer_since,
  COALESCE(acct.account_count, 0)        AS account_count,
  COALESCE(tx.transaction_count, 0)      AS transaction_count,
  COALESCE(tx.net_transaction_amount, 0) AS net_transaction_amount,
  COALESCE(od.overdraft_events, 0)       AS overdraft_events,
  od.lowest_balance,
  COALESCE(ln.loan_count, 0)             AS loan_count,
  COALESCE(ln.total_loan_amount, 0)      AS total_loan_amount
FROM `${PROJECT}.finchat_silver_${ENV}.customer` c
LEFT JOIN acct USING (customer_id)
LEFT JOIN tx   USING (customer_id)
LEFT JOIN od   USING (customer_id)
LEFT JOIN ln   USING (customer_id);

-- ---------- Analyst semantic perimeter (ADR-0018) -----------------------------
-- The ONLY relational surface exposed to conversational analytics. Curated
-- dim/fact views that structurally OMIT identifier columns (account_number,
-- full_name, email, natural keys) — minimization by design, not by prompt.
-- Amounts remain (the analytical point); CLS still applies via the source tags.
CREATE OR REPLACE VIEW `${PROJECT}.finchat_graph_${ENV}.dim_customer` AS
SELECT customer_id, segment, created_at
FROM `${PROJECT}.finchat_silver_${ENV}.customer`;

CREATE OR REPLACE VIEW `${PROJECT}.finchat_graph_${ENV}.dim_account` AS
SELECT account_id, customer_id, account_type, currency, status, opened_at
FROM `${PROJECT}.finchat_silver_${ENV}.account`;   -- account_number intentionally absent

CREATE OR REPLACE VIEW `${PROJECT}.finchat_graph_${ENV}.fact_transaction` AS
SELECT transaction_id, account_id, txn_type, amount, currency, status, event_time
FROM `${PROJECT}.finchat_silver_${ENV}.transaction`;  -- counterparty_account intentionally absent
