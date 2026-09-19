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
  latency_ms      INT64,
  -- Model pinning evidence (ADR-0022). `requested` is what the call site asked for;
  -- `served` is what the provider says actually answered, read from the Vertex response
  -- `modelVersion` field. They are different facts and only the second is auditable —
  -- `served` stays NULL when the surface does not report it rather than being back-filled.
  model_requested STRING,
  model_served    STRING
)
PARTITION BY DATE(ts)
CLUSTER BY persona, channel;

-- Existing deployments: additive, idempotent.
ALTER TABLE `${PROJECT}.finchat_eval_${ENV}.conversation_log`
  ADD COLUMN IF NOT EXISTS model_requested STRING,
  ADD COLUMN IF NOT EXISTS model_served    STRING;

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

-- Rolling 7-day live metrics (normalized 0..1) + sample size. Latency p50/p95 are
-- taken from conversation_log over ALL turns in the window (every turn has a latency,
-- scored or not) — the operational half of eval observability alongside quality.
CREATE OR REPLACE VIEW `${PROJECT}.finchat_eval_${ENV}.eval_summary` AS
SELECT
  COUNT(*)                                   AS n,
  MAX(scored_at)                             AS last_scored_at,
  ROUND(AVG(SAFE_DIVIDE(groundedness - 1, 4)), 3)          AS grounding_accuracy,
  ROUND(1 - AVG(SAFE_DIVIDE(groundedness - 1, 4)), 3)      AS hallucination_rate,
  ROUND(AVG(SAFE_DIVIDE(instruction_following - 1, 4)), 3) AS instruction_following,
  ROUND(AVG(SAFE_DIVIDE(coherence - 1, 4)), 3)             AS coherence,
  ROUND(AVG(safety), 3)                                    AS safety,
  ROUND(AVG(overall), 3)                                   AS overall,
  (SELECT APPROX_QUANTILES(latency_ms, 100)[OFFSET(50)]
     FROM `${PROJECT}.finchat_eval_${ENV}.conversation_log`
     WHERE latency_ms IS NOT NULL
       AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)) AS latency_p50_ms,
  (SELECT APPROX_QUANTILES(latency_ms, 100)[OFFSET(95)]
     FROM `${PROJECT}.finchat_eval_${ENV}.conversation_log`
     WHERE latency_ms IS NOT NULL
       AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)) AS latency_p95_ms
FROM `${PROJECT}.finchat_eval_${ENV}.conversation_scores`
WHERE scored_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);

-- =============================================================================
-- Conversation-level safety (ADR-0034).
-- conversation_log gains the conversation key it never had: before this, the SPA sent a
-- session id and the BFF dropped it, so every row was an island and nothing above the
-- single turn could be computed.
-- =============================================================================
ALTER TABLE `${PROJECT}.finchat_eval_${ENV}.conversation_log`
  ADD COLUMN IF NOT EXISTS session_key    STRING,   -- sha256(principal_hash:session_id)[:16]
  ADD COLUMN IF NOT EXISTS principal_hash STRING,   -- control_events.principal_hash(); 'anonymous' for signed-out customers
  ADD COLUMN IF NOT EXISTS turn_index     INT64;

-- turn_signals : one row per SCREENED customer turn — every turn, not a sample. Signal
-- names and confidences, the tier decided, the product action taken, and why. No text:
-- the FK to conversation_log is how a reviewer with dataset access reaches the words.
-- This is the evidence plane for the control (docs/26 §3): every execution, not just
-- the ones that fired, so "was any turn unscreened" is a query, not an assumption.
CREATE TABLE IF NOT EXISTS `${PROJECT}.finchat_eval_${ENV}.turn_signals`
(
  conversation_id  STRING NOT NULL,        -- FK -> conversation_log
  ts               TIMESTAMP NOT NULL,
  session_key      STRING NOT NULL,
  principal_hash   STRING NOT NULL,
  turn_index       INT64,
  persona          STRING,
  channel          STRING,
  armor_blocked    BOOL,                   -- Model Armor decided this turn
  armor_class      STRING,                 -- security | privacy | content
  agent_refused    BOOL,                   -- the assistant declined (classifier's read)
  signals          STRING,                 -- JSON {name: confidence} at >= med only
  top_signal       STRING,
  top_confidence   FLOAT64,
  signal_class     STRING,                 -- security | fraud | wellbeing | conduct
  tier             INT64,                  -- 0 none | 2 human review | 1 product action
  action           STRING,                 -- none | handoff_crisis | handoff_fraud | quarantine | withhold
  reasons          ARRAY<STRING>,          -- which thresholds crossed, as data
  classifier_error BOOL,                   -- the turn went UNSCREENED (fail-open served it)
  classifier_model STRING,                 -- what actually classified (ADR-0022)
  rationale        STRING,                 -- classifier's one line; never leaves the dataset
  latency_ms       INT64
)
PARTITION BY DATE(ts)
CLUSTER BY session_key, principal_hash;

-- session_trajectory : one row per session, the trajectory a reviewer opens the ticket
-- with. What the engine computed turn by turn, restated over the whole session.
CREATE OR REPLACE VIEW `${PROJECT}.finchat_eval_${ENV}.session_trajectory` AS
SELECT
  session_key,
  ANY_VALUE(principal_hash)                          AS principal_hash,
  MIN(ts)                                            AS started_at,
  MAX(ts)                                            AS last_turn_at,
  COUNT(*)                                           AS turns,
  COUNTIF(tier = 1)                                  AS product_actions,
  COUNTIF(tier = 2)                                  AS review_events,
  COUNTIF(signal_class = 'security'
          OR (armor_blocked AND armor_class = 'security')) AS security_hits,
  COUNTIF(signal_class = 'fraud')                    AS fraud_hits,
  COUNTIF(signal_class = 'wellbeing')                AS wellbeing_hits,
  COUNTIF(agent_refused)                             AS refusals,
  COUNTIF(armor_blocked)                             AS armor_blocks,
  COUNTIF(classifier_error)                          AS unscreened_turns,
  LOGICAL_OR(action = 'quarantine')                  AS quarantined,
  ARRAY_AGG(STRUCT(turn_index, top_signal, top_confidence, tier, action)
            ORDER BY turn_index)                     AS path
FROM `${PROJECT}.finchat_eval_${ENV}.turn_signals`
WHERE ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY session_key;

-- safety_buckets : the population series behind tier 3 (scripts/safety_anomaly.py).
-- Fifteen-minute buckets of rates, so "is the SYSTEM going wrong" — a probing campaign
-- across many sessions, refusals collapsing after a silent model change — is a deviation
-- from this baseline rather than a threshold someone guessed. model_served is carried
-- from conversation_log so a shift is attributable to the version that produced it.
CREATE OR REPLACE VIEW `${PROJECT}.finchat_eval_${ENV}.safety_buckets` AS
SELECT
  TIMESTAMP_BUCKET(s.ts, INTERVAL 15 MINUTE)                       AS bucket,
  COUNT(*)                                                         AS turns,
  COUNT(DISTINCT s.session_key)                                    AS sessions,
  SAFE_DIVIDE(COUNTIF(s.signal_class = 'security'
                      OR (s.armor_blocked AND s.armor_class = 'security')), COUNT(*)) AS security_rate,
  SAFE_DIVIDE(COUNTIF(s.signal_class IN ('fraud', 'wellbeing')), COUNT(*)) AS wellbeing_rate,
  SAFE_DIVIDE(COUNTIF(s.agent_refused), COUNT(*))                  AS refusal_rate,
  SAFE_DIVIDE(COUNTIF(s.classifier_error), COUNT(*))               AS unscreened_rate,
  COUNTIF(s.tier = 1)                                              AS product_actions,
  COUNTIF(s.tier = 2)                                              AS review_events,
  COUNT(DISTINCT IF(s.signal_class = 'security', s.session_key, NULL)) AS probing_sessions,
  ANY_VALUE(l.model_served)                                        AS model_served
FROM `${PROJECT}.finchat_eval_${ENV}.turn_signals` s
LEFT JOIN `${PROJECT}.finchat_eval_${ENV}.conversation_log` l USING (conversation_id)
WHERE s.ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 28 DAY)
GROUP BY bucket;
