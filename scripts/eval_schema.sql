-- =============================================================================
-- FinChat — Live evaluation (AgentOps) schema. Replace ${PROJECT}/${ENV}.
-- conversation_log : every agent/analyst turn, captured best-effort by the BFF.
-- conversation_scores : per-turn LLM-as-judge scores (Vertex Gen AI Eval).
-- eval_summary : rolling 7-day averages that drive the Admin -> Evaluations card.
-- Dataset finchat_eval_${ENV} is Terraform-managed (us-central1).
-- =============================================================================

CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_eval_${ENV}.conversation_log`
(
  conversation_id STRING NOT NULL,          -- PK
  ts              TIMESTAMP NOT NULL,
  persona         STRING,                    -- customer | analyst
  channel         STRING,                    -- agent | analytics | kb
  question        STRING,
  answer          STRING,
  context         STRING,                    -- JSON grounding context (sql, rows, sources)
  latency_ms      INT64
)
PARTITION BY DATE(ts)
CLUSTER BY persona, channel;

CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_eval_${ENV}.conversation_scores`
(
  conversation_id       STRING NOT NULL,     -- FK -> conversation_log
  scored_at             TIMESTAMP NOT NULL,
  channel               STRING,
  groundedness          FLOAT64,             -- 1..5 (Vertex pointwise) or NULL if no context
  instruction_following FLOAT64,             -- 1..5
  coherence             FLOAT64,             -- 1..5
  safety                FLOAT64,             -- 0/1
  overall               FLOAT64,             -- normalized 0..1 composite
  rationale             STRING,
  model_version         STRING
)
PARTITION BY DATE(scored_at)
CLUSTER BY channel;

-- Rolling 7-day live metrics (normalized 0..1) + sample size.
CREATE OR REPLACE VIEW `${PROJECT}.finchat_eval_${ENV}.eval_summary` AS
SELECT
  COUNT(*)                                   AS n,
  MAX(scored_at)                             AS last_scored_at,
  ROUND(AVG(SAFE_DIVIDE(groundedness - 1, 4)), 3)          AS grounding_accuracy,
  ROUND(1 - AVG(SAFE_DIVIDE(groundedness - 1, 4)), 3)      AS hallucination_rate,
  ROUND(AVG(SAFE_DIVIDE(instruction_following - 1, 4)), 3) AS instruction_following,
  ROUND(AVG(SAFE_DIVIDE(coherence - 1, 4)), 3)             AS coherence,
  ROUND(AVG(safety), 3)                                    AS safety,
  ROUND(AVG(overall), 3)                                   AS overall
FROM `${PROJECT}.finchat_eval_${ENV}.conversation_scores`
WHERE scored_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
