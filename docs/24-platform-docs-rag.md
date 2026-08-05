# 24 — Platform Docs RAG ("ask FinChat about FinChat")

> The repository's own documentation — architecture deliverables, ADRs, the governance
> bundle, product READMEs — as a searchable corpus, so *"why did we choose Bigtable over
> Spanner?"* and *"how does the agent registry enforce tool permissions?"* are answerable
> in the product rather than by grepping.

## Why it is a separate corpus

The obvious implementation is to add repo docs to `kb_chunks` alongside the fees and
branch-hours corpus. That would be a mistake.

`kb_chunks` is what a **customer** is answered from, and the Banking Assistant's
instruction is explicit: *ground your answer ONLY in the returned snippets*. Put ADRs in
that store and a customer asking about overdraft fees can be answered with agent-registry
internals — retrieval is semantic, and "overdraft" appears in the loan risk documentation
too. That is a persona and quality regression dressed as a feature.

So repo docs live in **`platform_chunks`**: same dataset, same embedding model, different
corpus and different audience.

| | `kb_chunks` | `platform_chunks` |
|---|---|---|
| Answers | Customers | Analysts / admins |
| Content | Fees, branch hours, policies, terms | Architecture, ADRs, governance, runbooks |
| Reached by | `search_knowledge_base` (Banking Assistant tool) | PLATFORM intent on the analyst router |
| Size | 22 documents | ~445 chunks across 94 files |

Sharing the embedding model is deliberate. Two models would be two things to keep on the
same version, and a corpus embedded with a different model than the query is a silent
relevance failure — nothing errors, results just quietly get worse.

## Chunking

[`scripts/build_repo_corpus.py`](../scripts/build_repo_corpus.py) splits on markdown
headings rather than fixed token windows. An ADR's *Decision* or *Consequences* section is
already a semantically complete unit; splitting mid-argument is how a RAG system starts
citing half a decision and sounding confident about it. Sections over 4,000 characters
split on paragraph boundaries with the heading trail repeated, so a chunk always knows
what it belongs to.

Two content rules:

- **Fenced code and tables are stripped.** Both retrieve badly, and neither answers the
  question this corpus is for — *"how does X work and why"*, not *"show me the SQL"*.
- **The source path is prepended to the content**, not merely stored beside it, so the
  model can cite where an answer came from without a second lookup.

```
architecture      174      docs/*.md
decision-record   154      docs/adr/*.md
governance         77      knowledge/**/*.md
product            27      products/*/README.md
overview            8      README.md
evaluation          5      eval/README.md
```

## Routing

The analyst router gained a fourth intent alongside ANALYTICS / KB / SEMANTICS:

> **PLATFORM** — a question about how the FinChat platform itself is built or operated:
> architecture, a design decision, a service, module, pipeline, the gateway, the agent
> registry, CI/CD, Terraform, runbooks, or what the platform supports. About the *system*,
> not the bank's data or the bank's policies.

`PLATFORM` is checked **first** when parsing the router's reply, because it is the most
specific intent — *"how is the analytics pipeline built"* contains tokens that would
otherwise match ANALYTICS. The keyword fallback weights platform terms ×2 for the same
reason: `adr`, `terraform`, `runbook` are specific where `total` and `policy` are common.

The answer prompt instructs the model to cite source paths and to say when the
documentation does not cover something — a confident wrong answer about our own
architecture is worse than no answer.

## Keeping it current

**The corpus is a build artifact of the repo and goes stale the moment an ADR lands.**
Re-run after documentation changes:

```bash
./products/transactions/agent/kb/setup_platform_rag.sh prod
```

Embedding ~450 chunks costs cents, so re-running is cheaper than reasoning about whether
it is needed. This is deliberately not wired into CI yet — see the limits below.

## Verifying retrieval

```sql
SELECT base.source_path, ROUND(distance, 3) AS dist
FROM VECTOR_SEARCH(
  TABLE `strongsville-city-schools.finchat_kb_prod.platform_chunks`, 'embedding',
  (SELECT ml_generate_embedding_result AS embedding FROM ML.GENERATE_EMBEDDING(
     MODEL `strongsville-city-schools.finchat_kb_prod.embedding_model`,
     (SELECT 'how does the agent registry enforce tool permissions?' AS content),
     STRUCT(TRUE AS flatten_json_output))),
  top_k => 4, distance_type => 'COSINE')
```

Returns `docs/adr/0023-agent-registry-and-identity.md` and `docs/20-agent-registry.md` at
distances 0.289–0.309.

## Known limits

- **No CI refresh.** A merged ADR is not searchable until someone re-runs the script. The
  honest fix is a build-deploy step; until then the corpus can lag the repo, and an answer
  can be confidently out of date. This is the most likely way it misleads.
- **No hybrid retrieval or reranking.** The customer KB has BM25 + RRF + a cross-encoder
  rerank; this path is dense-only. Exact-token questions ("what does `DRIFT-3` do") are
  the weak case — dense embeddings are poor at rare literal tokens, which is exactly why
  hybrid exists on the other corpus.
- **Prose only.** Code, Terraform and SQL are excluded, so *"show me the registry schema"*
  is answered from the doc's description of it rather than from `main.tf`.
- **Not in the model inventory as a distinct model.** It reuses the M4 retriever
  (`text-embedding-005`) and the M5-class generation path; it adds a corpus, not a model.
- **No eval set.** Retrieval quality here is spot-checked, not measured. The customer KB
  at least rides the grounding-accuracy gate; this corpus has nothing equivalent.
