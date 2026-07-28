"""Hybrid retrieval tests — run offline against the real KB corpus, no GCP needed.

The claim being tested is specific: a dense-only index is weak on rare exact tokens
(product names, acronyms, zips, amounts), and BM25 fixes exactly those. These assert it
on the shipped corpus rather than taking it on faith.

Run: `pytest test_retrieval.py -q`
"""
import json
import pathlib

import pytest

import retrieval

HERE = pathlib.Path(__file__).resolve().parent
CORPUS = HERE / "kb" / "corpus.jsonl"
DOCS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- packaging guard -----------------------------------------------------------
# Modules that deliberately do NOT ship in the runtime image. Anything not listed here
# must be in the Dockerfile COPY — adding a file forces a conscious choice between the two.
NOT_IN_IMAGE = {
    "deploy.py",   # operator script: `python deploy.py --project ...`, run from a workstation
}


def test_every_runtime_module_is_copied_into_the_image():
    """The Dockerfile COPYs modules by name, so a new file that is not listed is simply
    absent at runtime and fails to import — which is how retrieval.py silently took the
    knowledge base down after Inc 23. This guard fails the build instead.
    """
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    copied = " ".join(ln for ln in dockerfile.splitlines() if ln.strip().startswith("COPY"))
    missing = [
        p.name for p in sorted(HERE.glob("*.py"))
        if not p.name.startswith("test_") and p.name not in NOT_IN_IMAGE and p.name not in copied
    ]
    assert not missing, f"not COPYed into the agent image: {missing}"


# --- tokenisation: the whole reason sparse retrieval helps here -----------------
@pytest.mark.parametrize("text,expected", [
    ("$225", "$225"),
    ("8.99%", "8.99%"),
    ("44107", "44107"),
    ("NSF", "nsf"),
    ("Allpoint", "allpoint"),
])
def test_tokenizer_preserves_exact_forms(text, expected):
    """Currency, percent and zip forms must survive — splitting them is what loses them."""
    assert expected in retrieval.tokenize(text)


def test_tokenizer_drops_single_chars():
    assert "a" not in retrieval.tokenize("a $35 fee")
    assert "$35" in retrieval.tokenize("a $35 fee")


# --- BM25 on the real corpus ---------------------------------------------------
@pytest.mark.parametrize("query,expected_doc", [
    ("NSF", "fees-overdraft"),
    ("Allpoint", "atm-network"),
    ("44107", "branch-lakewood"),
    ("$225", "funds-availability"),
    ("8.99%", "loan-rates"),
    ("Premier Savings", "savings-terms"),
])
def test_bm25_finds_exact_tokens(query, expected_doc):
    """These are the queries a dense-only index handles badly. Sparse must nail them."""
    ranked = retrieval.bm25_rank(query, DOCS)
    assert ranked, f"no BM25 hit for {query!r}"
    assert ranked[0][0] == expected_doc, f"{query!r} -> {ranked[0][0]}, expected {expected_doc}"


def test_bm25_returns_nothing_for_unmatched_terms():
    """No lexical overlap must score nothing — sparse should abstain, not guess. This is
    precisely the case where the dense retriever carries the query."""
    assert retrieval.bm25_rank("cryptocurrency staking rewards", DOCS) == []


def test_bm25_ignores_pure_stopword_overlap():
    """A doc should not win on common words alone; IDF has to do its job."""
    ranked = retrieval.bm25_rank("what is the", DOCS)
    assert len(ranked) < len(DOCS)


# --- fusion --------------------------------------------------------------------
def test_rrf_ranks_agreement_first():
    """A doc both retrievers found should outrank one only a single retriever found."""
    fused = retrieval.rrf_fuse(["a", "b", "c"], ["c", "d"])
    assert fused[0][0] == "c"
    doc_c = next(f for f in fused if f[0] == "c")
    assert doc_c[2] is True and doc_c[3] is True     # in_dense and in_sparse


def test_rrf_keeps_sparse_only_rescues():
    """The point of hybrid: a doc dense never returned still reaches the candidate set."""
    fused = retrieval.rrf_fuse(["a", "b"], ["z"])
    ids = [f[0] for f in fused]
    assert "z" in ids
    z = next(f for f in fused if f[0] == "z")
    assert z[2] is False and z[3] is True


def test_rrf_handles_empty_retriever():
    """If one retriever returns nothing the other must still produce results."""
    assert [f[0] for f in retrieval.rrf_fuse([], ["a", "b"])] == ["a", "b"]


# --- reranker parsing: must never raise ----------------------------------------
@pytest.mark.parametrize("text,n,expected", [
    ("[3,1,2]", 4, [2, 0, 1]),
    ("Here you go: [2, 1]", 3, [1, 0]),
    ("[1,1,2]", 3, [0, 1]),          # duplicates collapse
    ("[9,1]", 2, [0]),               # out-of-range dropped
    ("no json here", 3, []),         # garbage -> caller keeps fused order
    ("", 3, []),
])
def test_parse_rerank_is_robust(text, n, expected):
    assert retrieval.parse_rerank(text, n) == expected


# --- SQL shape -----------------------------------------------------------------
def test_hybrid_sql_contains_both_retrievers_and_fusion():
    sql = retrieval.hybrid_sql("proj", "ds")
    for fragment in ("VECTOR_SEARCH", "ML.GENERATE_EMBEDDING", "bm25", "LOGICAL_OR", "@q"):
        assert fragment in sql, f"missing {fragment}"


def test_hybrid_sql_parameterises_the_query_text_only():
    """The user's question must always travel as @q. The k values are ints we control and
    are interpolated as literals because VECTOR_SEARCH's top_k needs a literal."""
    sql = retrieval.hybrid_sql("proj", "ds", k_each=5, k_cand=6)
    assert "@q" in sql
    assert "top_k => 5" in sql and "LIMIT 6" in sql
    assert "@k_each" not in sql and "@k_cand" not in sql


def test_hybrid_sql_coerces_k_values_to_int():
    """Belt and braces: k values reach the SQL text, so they must never be raw strings."""
    sql = retrieval.hybrid_sql("proj", "ds", k_each="7", k_cand="9")
    assert "top_k => 7" in sql and "LIMIT 9" in sql


def test_hybrid_sql_has_no_identifier_collisions():
    """CTEs named the same as their columns (tf/df) parse but are a trap — keep them apart."""
    sql = retrieval.hybrid_sql("proj", "ds")
    assert "tf_count" in sql and "df_count" in sql
    assert "tfreq" in sql and "dfreq" in sql
