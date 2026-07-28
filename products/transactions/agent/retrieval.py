"""Hybrid retrieval for the knowledge base: dense + sparse, fused, then reranked.

Why this exists
---------------
The KB was dense-only (BigQuery ``VECTOR_SEARCH`` over ``ML.GENERATE_EMBEDDING``).
Embeddings are excellent at paraphrase ("what happens if I spend more than I have?"
-> the overdraft policy) and poor at rare exact tokens — a product name (``Allpoint``),
an acronym (``NSF``), a zip (``44107``), an amount (``$225``). Those are precisely the
tokens a banking user types. Dense and sparse fail differently, so we run both and fuse.

Cost shape (this stays a near-zero-cost platform)
-------------------------------------------------
* Sparse BM25 is computed **in the same BigQuery query** as the vector search — one job,
  no search index, so no index storage or maintenance cost. The corpus is kilobytes.
* Fusion (reciprocal rank fusion) is SQL — free.
* Reranking is one small ``gemini-2.5-flash`` call, the model this platform already calls
  per request for intent classification. Fractions of a cent, scale-to-zero.

At real corpus scale the SQL BM25 (a full scan) stops being viable: you would move to a
BigQuery ``SEARCH INDEX`` or a dedicated search service, and to a cross-encoder or the
Vertex Ranking API. The pipeline shape does not change — only the cost line does.

The pure-Python helpers below are the offline/demo path **and** the specification the SQL
mirrors, which is what makes the ranking testable without touching GCP.
"""
from __future__ import annotations

import math
import re

# Keeps $, %, and internal dots attached so "$225", "8.99%", "44107" and "e-statements"
# survive tokenisation — the whole point of having a sparse retriever.
_TOKEN_RE = re.compile(r"\$?[a-z0-9][a-z0-9.\-]*%?")

# BM25 parameters (Robertson/Sparck-Jones defaults).
K1 = 1.2
B = 0.75
RRF_K = 60          # standard reciprocal-rank-fusion damping constant


def tokenize(text: str) -> list[str]:
    """Lowercase, then extract terms preserving currency/percent/zip forms."""
    return [t.strip(".-") for t in _TOKEN_RE.findall((text or "").lower()) if len(t.strip(".-")) > 1]


def bm25_rank(query: str, docs: list[dict]) -> list[tuple[str, float]]:
    """Score docs against the query with BM25. `docs` need `doc_id`, `title`, `content`.

    Returns (doc_id, score) for scoring docs only, best first. This is the exact math the
    BigQuery sparse CTE implements — keep the two in step.
    """
    q_terms = set(tokenize(query))
    if not q_terms or not docs:
        return []

    toks = {d["doc_id"]: tokenize(f"{d.get('title','')} {d.get('content','')}") for d in docs}
    n_docs = len(docs)
    avgdl = sum(len(t) for t in toks.values()) / n_docs if n_docs else 0.0
    if not avgdl:
        return []

    df = {t: sum(1 for tk in toks.values() if t in tk) for t in q_terms}

    scored: list[tuple[str, float]] = []
    for doc_id, tk in toks.items():
        dl = len(tk)
        score = 0.0
        for t in q_terms:
            tf = tk.count(t)
            if not tf:
                continue
            idf = math.log(1 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * dl / avgdl))
        if score > 0:
            scored.append((doc_id, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


def rrf_fuse(dense_ids: list[str], sparse_ids: list[str]) -> list[tuple[str, float, bool, bool]]:
    """Reciprocal rank fusion of two ranked id lists.

    Returns (doc_id, fused_score, in_dense, in_sparse) best first. Carrying the two
    provenance flags is deliberate: it is how you show a sparse-only rescue (an exact
    token the embedding missed) rather than asserting hybrid helped.
    """
    scores: dict[str, float] = {}
    d_set, s_set = set(dense_ids), set(sparse_ids)
    for rank, doc_id in enumerate(dense_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, doc_id in enumerate(sparse_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    fused = [(doc_id, s, doc_id in d_set, doc_id in s_set) for doc_id, s in scores.items()]
    fused.sort(key=lambda x: (-x[1], x[0]))
    return fused


def rerank_prompt(query: str, candidates: list[dict]) -> str:
    """Prompt for the LLM reranker: score each candidate's relevance to the query.

    A reranker sees the query and the passage *together* (unlike embeddings, which are
    computed independently), which is why it recovers precision that retrieval loses.
    """
    lines = [
        "Rank these knowledge-base snippets by how well each ANSWERS the user's question.",
        "Judge relevance to the question only — not writing quality or length.",
        "Reply with ONLY a JSON array of snippet numbers, best first, e.g. [3,1,4].",
        "Omit any snippet that does not help answer the question.",
        f"\nQuestion: {query}\n",
    ]
    for i, c in enumerate(candidates, start=1):
        body = (c.get("content") or "")[:600]
        lines.append(f"[{i}] {c.get('title','')} — {body}")
    lines.append("\nJSON array:")
    return "\n".join(lines)


def parse_rerank(text: str, n: int) -> list[int]:
    """Parse the model's JSON array into 0-based indices, ignoring anything malformed.

    Never raises: a bad rerank response must degrade to the fused order, not break search.
    """
    nums = re.findall(r"\d+", text or "")
    out, seen = [], set()
    for raw in nums:
        i = int(raw) - 1
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def hybrid_sql(project: str, dataset: str, k_each: int = 8, k_cand: int = 8) -> str:
    """One BigQuery job: dense VECTOR_SEARCH + BM25 sparse + RRF fusion.

    Deliberately one query rather than two round trips — fewer jobs, less latency, and
    the fusion happens where the data already is.

    `k_each`/`k_cand` are interpolated as literals, not query parameters: VECTOR_SEARCH's
    `top_k =>` argument expects a literal. They are ints coerced here, never user input,
    so there is no injection surface — the actual query text stays parameterised via @q.
    """
    tbl = f"`{project}.{dataset}.kb_chunks`"
    model = f"`{project}.{dataset}.embedding_model`"
    k_each, k_cand = int(k_each), int(k_cand)
    return f"""
    WITH
    qtok AS (
      SELECT DISTINCT tok
      FROM UNNEST(REGEXP_EXTRACT_ALL(LOWER(@q), r'\\$?[a-z0-9][a-z0-9.\\-]*%?')) AS tok
      WHERE LENGTH(tok) > 1
    ),
    docs AS (
      SELECT doc_id, title, category, content,
             REGEXP_EXTRACT_ALL(LOWER(CONCAT(title, ' ', content)),
                                r'\\$?[a-z0-9][a-z0-9.\\-]*%?') AS toks
      FROM {tbl}
    ),
    stats AS (SELECT COUNT(*) AS n_docs, AVG(ARRAY_LENGTH(toks)) AS avgdl FROM docs),
    tfreq AS (
      SELECT d.doc_id AS doc_id, q.tok AS tok, ARRAY_LENGTH(d.toks) AS dl,
             (SELECT COUNT(*) FROM UNNEST(d.toks) AS t WHERE t = q.tok) AS tf_count
      FROM docs d CROSS JOIN qtok q
    ),
    dfreq AS (
      SELECT tok, COUNTIF(tf_count > 0) AS df_count FROM tfreq GROUP BY tok
    ),
    bm25 AS (
      SELECT tfreq.doc_id AS doc_id,
             SUM(LN(1 + (stats.n_docs - dfreq.df_count + 0.5) / (dfreq.df_count + 0.5))
                 * (tfreq.tf_count * ({K1} + 1))
                 / (tfreq.tf_count + {K1} * (1 - {B} + {B} * tfreq.dl / stats.avgdl))) AS score
      FROM tfreq JOIN dfreq USING (tok) CROSS JOIN stats
      WHERE tfreq.tf_count > 0
      GROUP BY tfreq.doc_id
    ),
    sparse AS (
      SELECT doc_id, ROW_NUMBER() OVER (ORDER BY score DESC, doc_id) AS rnk FROM bm25
    ),
    dense AS (
      SELECT base.doc_id AS doc_id, ROW_NUMBER() OVER (ORDER BY distance ASC) AS rnk
      FROM VECTOR_SEARCH(
        TABLE {tbl}, 'embedding',
        (SELECT ml_generate_embedding_result AS embedding
         FROM ML.GENERATE_EMBEDDING({model}, (SELECT @q AS content),
                                    STRUCT(TRUE AS flatten_json_output))),
        top_k => {k_each}, distance_type => 'COSINE')
    ),
    ranked AS (
      SELECT doc_id, 'dense' AS src, 1 / ({RRF_K} + rnk) AS w FROM dense
      UNION ALL
      SELECT doc_id, 'sparse' AS src, 1 / ({RRF_K} + rnk) AS w
      FROM sparse WHERE rnk <= {k_each}
    ),
    fused AS (
      SELECT doc_id,
             SUM(w) AS rrf,
             LOGICAL_OR(src = 'dense')  AS in_dense,
             LOGICAL_OR(src = 'sparse') AS in_sparse
      FROM ranked
      GROUP BY doc_id
    )
    SELECT d.doc_id, d.title, d.category, d.content, f.rrf, f.in_dense, f.in_sparse
    FROM fused f JOIN {tbl} d USING (doc_id)
    ORDER BY f.rrf DESC
    LIMIT {k_cand}
    """
