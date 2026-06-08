-- =============================================================================
-- Backfill the customer + account DIMENSION tables from observed transactions.
-- The streaming generator emits only transaction facts; this derives a 1:1
-- customer/account per distinct account_id so the Gold balance/summary views
-- (which join FROM account) return data. Deterministic + idempotent (MERGE).
-- Replace ${PROJECT} / ${ENV}.
-- =============================================================================

-- Distinct accounts seen in the ledger.
CREATE TEMP TABLE _accts AS
SELECT account_id, ANY_VALUE(currency) AS currency
FROM `${PROJECT}.finchat_silver_${ENV}.transaction`
GROUP BY account_id;

-- One synthetic customer per account.
MERGE `${PROJECT}.finchat_silver_${ENV}.customer` T
USING (
  SELECT
    CONCAT('cust-', account_id)                                              AS customer_id,
    TO_HEX(SHA256(account_id))                                               AS customer_natural_key,
    CONCAT('Customer ', UPPER(SUBSTR(account_id, 1, 8)))                     AS full_name,
    CONCAT('user_', SUBSTR(account_id, 1, 8), '@example.com')               AS email,
    ['RETAIL','PREMIER','STUDENT','BUSINESS'][OFFSET(MOD(ABS(FARM_FINGERPRINT(account_id)), 4))] AS segment,
    TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 400 DAY)                     AS created_at,
    CURRENT_TIMESTAMP()                                                      AS ingest_time,
    'seed-1.0.0'                                                             AS pipeline_version
  FROM _accts
) S
ON T.customer_id = S.customer_id
WHEN NOT MATCHED THEN INSERT ROW;

-- The account record (links to its customer).
MERGE `${PROJECT}.finchat_silver_${ENV}.account` T
USING (
  SELECT
    account_id                                                              AS account_id,
    CAST(MOD(ABS(FARM_FINGERPRINT(account_id)), 9000000000) + 1000000000 AS STRING) AS account_number,
    CONCAT('cust-', account_id)                                             AS customer_id,
    ['CHECKING','SAVINGS'][OFFSET(MOD(ABS(FARM_FINGERPRINT(account_id)), 2))] AS account_type,
    currency                                                                AS currency,
    'ACTIVE'                                                                AS status,
    TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 365 DAY)                    AS opened_at,
    CURRENT_TIMESTAMP()                                                     AS ingest_time,
    'seed-1.0.0'                                                            AS pipeline_version
  FROM _accts
) S
ON T.account_id = S.account_id
WHEN NOT MATCHED THEN INSERT ROW;
