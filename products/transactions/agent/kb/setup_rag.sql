-- =============================================================================
-- RAG setup: remote embedding model + chunk embeddings (BigQuery vector).
-- Run AFTER `terraform apply` (creates the KB dataset + connection) and AFTER
-- loading kb_raw (see setup_rag.sh). Replace ${PROJECT} ${ENV} ${REGION} ${CONN}.
-- =============================================================================

-- 1) Remote embedding model (calls Vertex via the BigQuery connection).
CREATE OR REPLACE MODEL `${PROJECT}.finchat_kb_${ENV}.embedding_model`
REMOTE WITH CONNECTION `${PROJECT}.${REGION}.${CONN}`
OPTIONS (ENDPOINT = 'text-embedding-005');

-- 2) Generate embeddings for each chunk -> kb_chunks (the vector store).
CREATE OR REPLACE TABLE `${PROJECT}.finchat_kb_${ENV}.kb_chunks` AS
SELECT
  doc_id, title, category, content,
  ml_generate_embedding_result AS embedding
FROM ML.GENERATE_EMBEDDING(
  MODEL `${PROJECT}.finchat_kb_${ENV}.embedding_model`,
  (SELECT doc_id, title, category, content FROM `${PROJECT}.finchat_kb_${ENV}.kb_raw`),
  STRUCT(TRUE AS flatten_json_output)
);

-- 3) (Optional, for scale) a vector index. Brute-force VECTOR_SEARCH is fine for
--    a small corpus; an index needs >=5,000 rows to build, so it's commented out.
-- CREATE VECTOR INDEX kb_idx ON `${PROJECT}.finchat_kb_${ENV}.kb_chunks`(embedding)
--   OPTIONS (index_type = 'IVF', distance_type = 'COSINE');

-- Smoke test:
-- SELECT base.title, base.category, distance
-- FROM VECTOR_SEARCH(
--   TABLE `${PROJECT}.finchat_kb_${ENV}.kb_chunks`, 'embedding',
--   (SELECT ml_generate_embedding_result AS embedding FROM ML.GENERATE_EMBEDDING(
--     MODEL `${PROJECT}.finchat_kb_${ENV}.embedding_model`,
--     (SELECT 'what are the overdraft fees?' AS content),
--     STRUCT(TRUE AS flatten_json_output))),
--   top_k => 4, distance_type => 'COSINE');
