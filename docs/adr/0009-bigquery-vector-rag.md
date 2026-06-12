# ADR-0009 — Retrieval-Augmented Generation via BigQuery vector search

- **Status:** Accepted
- **Date:** 2026-06-07
- **Deciders:** Principal Data Architect
- **Context tags:** Agentic AI, RAG, data platform

## Context

The conversational agent grounds *structured* answers (balances, transactions) through DaaS tool
calls, but had no way to answer *unstructured* questions — fees, policies, terms & conditions, branch
hours/locations, lending info. Those answers must be grounded in governed documents, not hallucinated.

## Decision

Implement **RAG using BigQuery as the vector store**: embed a curated document corpus with a BigQuery
**remote embedding model** (`ML.GENERATE_EMBEDDING` → Vertex `text-embedding`), store chunks +
embeddings in a `kb_chunks` table, and retrieve with **`VECTOR_SEARCH`** (cosine, top-k). The agent
gets a `search_knowledge_base` tool that runs the search and grounds Gemini in the returned snippets.

## Rationale

- **No new datastore.** The platform already centers on BigQuery; keeping vectors there avoids standing
  up a separate vector DB (Pinecone/Vertex Vector Search) — cheaper, simpler, same governance/IAM/audit
  plane. At small corpus size, brute-force `VECTOR_SEARCH` is instant; a `VECTOR INDEX` (IVF) is a
  one-line add at scale (≥5k rows).
- **Embeddings without client code.** `ML.GENERATE_EMBEDDING` runs in BigQuery via a `CLOUD_RESOURCE`
  connection, so ingestion + query embedding are pure SQL.
- **Governed retrieval.** The KB is a normal BigQuery dataset: least-privilege grants (agent SA gets
  `dataViewer` + `connectionUser`), audit logging, lineage — same controls as the rest of the platform.

## Consequences

- New `bigquery_rag` Terraform module (connection → `aiplatform.user`, KB dataset, reader grants) +
  `kb/corpus.jsonl` + `kb/setup_rag.sql`/`setup_rag.sh` (one-time embed build).
- Agent tool `search_knowledge_base`; `KB_DATASET` env on the agent service.
- Small per-query embedding cost; negligible at chat volume.
- **Enterprise scale:** add a `VECTOR INDEX`, scheduled re-embedding on corpus change, chunking of large
  docs, and (optionally) Vertex AI Search / Vector Search if recall/scale demands it — the agent tool
  contract is unchanged. Full corpus/embedding lifecycle (event-driven ingestion, incremental re-embed,
  blue/green model upgrades, retrieval-quality evals) is documented in
  [docs/11 — KB corpus & embedding lifecycle](../11-future-state-roadmap.md#knowledge-base-corpus--embedding-lifecycle-enterprise-tier).

## Alternatives considered

- **Dedicated vector DB / Vertex Vector Search:** more capable at massive scale, but extra service,
  cost, and a second governance surface — unjustified for this corpus.
- **Stuff docs into the prompt:** doesn't scale, no retrieval, blows context + cost.
