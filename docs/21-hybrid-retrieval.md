# 21 — Hybrid retrieval: dense + sparse + rerank (Inc 23)

## Problem

The knowledge base was **dense-only**: BigQuery `VECTOR_SEARCH` over
`ML.GENERATE_EMBEDDING`, top-4, no keyword index and no reranking.

Embeddings are strong on paraphrase and weak on **rare exact tokens** — and rare exact
tokens are what banking users actually type: a network name (`Allpoint`), an acronym
(`NSF`), a branch zip (`44107`), a threshold (`$225`), a rate (`8.99%`). A dense index
returns something topically adjacent and confidently wrong.

## Solution

Run both retrievers, fuse, then rerank.

```
question
  ├─ dense  : VECTOR_SEARCH (embeddings)        → paraphrase-tolerant
  └─ sparse : BM25 in SQL                        → exact tokens, codes, amounts
        └─ RRF fusion (k=60) → candidates
              └─ Gemini Flash reranker → top N
```

All of dense, sparse and fusion happen in **one BigQuery job**
(`retrieval.hybrid_sql`). Reranking is one small `gemini-2.5-flash` call.

## Why both, concretely

Measured against the shipped corpus (`test_retrieval.py`, offline, no GCP):

| Query | BM25 (sparse) result |
|---|---|
| `NSF` | ✅ `fees-overdraft` |
| `Allpoint` | ✅ `atm-network` |
| `44107` | ✅ `branch-lakewood` |
| `$225` | ✅ `funds-availability` |
| `8.99%` | ✅ `loan-rates` |
| *"what happens if I spend more than I have"* | ❌ `security-tips` — matches on common words |

That last row is the point. Sparse fails the paraphrase; dense answers it. They fail
**differently**, which is why fusion beats either alone — not redundancy, coverage of two
distinct failure modes.

## Cost

This remains a near-zero-cost platform:

| Component | Cost |
|---|---|
| Sparse BM25 | Computed in the **same** BigQuery job as the vector search. **No search index** → no index storage or maintenance. Corpus is kilobytes; effectively inside the on-demand free tier. |
| RRF fusion | SQL. Free. |
| Reranker | One `gemini-2.5-flash` call per KB question — the model already called per request for intent classification. Fractions of a cent. |
| Infrastructure | **None added.** Nothing always-on; everything scales to zero. |

The delta per KB question is one extra LLM call (a few hundred ms) and a marginally
larger BigQuery query over a tiny table.

**Where this stops being free:** at real corpus scale the SQL BM25 is a full scan. You
would move to a BigQuery `SEARCH INDEX` (storage + maintenance cost past the free tier) or
a dedicated search service, and swap the LLM reranker for a cross-encoder or the Vertex
Ranking API (per-1k-queries pricing). **The pipeline shape does not change — only the cost
line does.** That is the honest enterprise framing.

## Degradation

Retrieval must never break on an optional stage:

- Reranker error, timeout, or unparseable output → fall back to fusion order.
- `KB_RERANK=0` → disable reranking entirely (kill switch).
- Sparse finds nothing (no lexical overlap) → dense results carry the query, unchanged.

## Not implemented: neighbour-chunk expansion

The RAG reference architecture includes expanding each hit with its `prev`/`next` chunks
so answers are not cut mid-clause. **This corpus does not need it** — the 22 documents are
short and atomic (avg ~285 chars), one chunk each, with no neighbours to expand into.
Implementing it here would be theatre. It becomes necessary the moment real policy PDFs
are chunked, and the schema would need `prev_id` / `next_id` on `kb_chunks`.

## Knobs

| Env var | Default | Meaning |
|---|---|---|
| `KB_K_EACH` | 8 | Depth per retriever before fusion |
| `KB_K_CANDIDATES` | 8 | Fused candidates sent to the reranker |
| `KB_TOP_N` | 4 | Snippets the agent finally sees |
| `KB_RERANK` | on | Kill switch |

## Observability

Each returned snippet carries `retriever`: `dense`, `sparse`, or `hybrid`. That is how you
demonstrate a **sparse rescue** — a document the embedding never surfaced — rather than
asserting hybrid helped.
