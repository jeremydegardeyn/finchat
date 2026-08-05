-- =============================================================================
-- Platform-docs RAG: the repo's own documentation as a searchable corpus.
--
-- Deliberately a SEPARATE table from kb_chunks. That store answers CUSTOMERS
-- (fees, branch hours, terms) and the Banking Assistant is instructed to ground
-- answers only in what it returns — so mixing ADRs in means a customer asking
-- about overdraft fees can be answered with agent-registry internals. Same
-- dataset, same embedding model, different corpus and different audience.
--
-- Run AFTER setup_rag.sql (which creates the embedding model) and AFTER loading
-- platform_raw. Replace ${PROJECT} ${ENV}.
-- =============================================================================

-- Embeddings over the repo docs -> platform_chunks (the analyst/admin vector store).
-- Reuses finchat_kb_${ENV}.embedding_model rather than declaring a second one: two
-- models would be two things to keep on the same version, and a corpus embedded with
-- a different model than the query is a silent relevance failure.
CREATE OR REPLACE TABLE `${PROJECT}.finchat_kb_${ENV}.platform_chunks` AS
SELECT
  doc_id, title, category, source_path, content,
  ml_generate_embedding_result AS embedding
FROM ML.GENERATE_EMBEDDING(
  MODEL `${PROJECT}.finchat_kb_${ENV}.embedding_model`,
  (SELECT doc_id, title, category, source_path, content
   FROM `${PROJECT}.finchat_kb_${ENV}.platform_raw`),
  STRUCT(TRUE AS flatten_json_output)
);

-- Sanity check the caller should read rather than assume:
--   SELECT category, COUNT(*) FROM `${PROJECT}.finchat_kb_${ENV}.platform_chunks`
--   GROUP BY 1 ORDER BY 2 DESC;
--
-- Example search (what the BFF runs):
--   SELECT base.title, base.source_path, distance
--   FROM VECTOR_SEARCH(
--     TABLE `${PROJECT}.finchat_kb_${ENV}.platform_chunks`, 'embedding',
--     (SELECT ml_generate_embedding_result AS embedding FROM ML.GENERATE_EMBEDDING(
--        MODEL `${PROJECT}.finchat_kb_${ENV}.embedding_model`,
--        (SELECT 'how does the agent registry enforce tool permissions?' AS content),
--        STRUCT(TRUE AS flatten_json_output))),
--     top_k => 5, distance_type => 'COSINE');
