"""The knowledge plane, served over MCP from the same SSOT the agents use.

`ui/_okf_context.py` is generated from `knowledge/ontology.yaml` and the OKF
concept docs by `scripts/compile_okf.py`, and CI fails if it drifts. Importing it
here rather than restating any of it means an MCP client and the platform's own
analyst agent are grounded on one artifact — which is the actual argument for
putting the knowledge on the protocol at all.

Tools return data. Resources return *meaning*: what a metric is certified to mean,
which joins are legitimate, what the agent is not permitted to answer. A client
that reads only the tools will invent the semantics; that is how "revenue" quietly
becomes something nobody signed off on.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import loader

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
CONTRACTS_DIR = REPO_ROOT / "contracts"


@lru_cache(maxsize=1)
def okf():
    """The compiled OKF context module (dependency-free, committed, CI-guarded)."""
    return loader.load("finchat_okf_context", REPO_ROOT / "ui" / "_okf_context.py")


def perimeter() -> dict:
    """The tables an analyst surface may reach, by dataset role."""
    return dict(okf().ANALYST_PERIMETER)


def join_bullets() -> str:
    return okf().ANALYST_JOIN_BULLETS


def refusal_bullets() -> str:
    return okf().ANALYST_REFUSAL_BULLETS


def refusal_rules() -> dict:
    return dict(okf().ANALYST_REFUSALS)


def glossary() -> list[dict]:
    return list(okf().ANALYST_GLOSSARY)


def stewardship() -> dict:
    return dict(okf().CONCEPT_STEWARDSHIP)


# --- the concept corpus ------------------------------------------------------
@lru_cache(maxsize=1)
def _sections() -> list[tuple[str, str]]:
    """The OKF corpus split back into its `### path` sections.

    `compile_okf.py` concatenates the concept docs with a `### <relative path>`
    header per document. Splitting on that gives addressable sections without a
    second copy of the corpus or a second parse of the source tree.
    """
    out: list[tuple[str, str]] = []
    title, buf = None, []
    for line in okf().ANALYST_KNOWLEDGE.splitlines():
        if line.startswith("### "):
            if title:
                out.append((title, "\n".join(buf).strip()))
            title, buf = line[4:].strip(), []
        else:
            buf.append(line)
    if title:
        out.append((title, "\n".join(buf).strip()))
    return out


def section_names() -> list[str]:
    return [name for name, _ in _sections()]


def get_section(name: str) -> str | None:
    want = name.strip().lower()
    for sec, body in _sections():
        if sec.lower() == want or Path(sec).stem.lower() == want:
            return body
    return None


@lru_cache(maxsize=1)
def _retrieval():
    """The KB's BM25 implementation, reused rather than reimplemented.

    `products/transactions/agent/retrieval.py` is pure standard library and already
    carries 24 tests plus a tokenizer that survives `$225`, `8.99%` and `44107`
    (docs/21). A second ranker here would be a second thing to keep in step, and
    the naive version — summed term frequency — ranks by document length, so
    "do pending transactions count" returns the longest document that says
    "transaction" rather than the glossary entry that answers the question.
    """
    return loader.load(
        "finchat_retrieval",
        REPO_ROOT / "products" / "transactions" / "agent" / "retrieval.py")


def search_sections(query: str, limit: int = 3) -> list[dict]:
    """Rank corpus sections against a query with BM25.

    Deliberately lexical and local. The corpus is 22 short sections, so an
    embedding call would add latency, cost and a network dependency to beat a scan
    of twenty-two documents. The policy KB behind the platform's own
    `search_knowledge_base` is a different corpus with a different failure mode,
    and it gets the hybrid dense+sparse treatment for that reason (docs/21).
    """
    bodies = dict(_sections())
    docs = [{"doc_id": name, "title": name, "content": body}
            for name, body in bodies.items()]
    ranked = _retrieval().bm25_rank(query, docs)[:limit]
    return [{"section": name, "score": round(score, 3), "content": bodies[name]}
            for name, score in ranked]


def find_term(term: str) -> dict | None:
    want = term.strip().lower()
    for entry in glossary():
        if entry.get("term", "").lower() == want:
            return entry
    for entry in glossary():
        if want in entry.get("term", "").lower():
            return entry
    return None


# --- published artifacts -----------------------------------------------------
def contracts() -> list[str]:
    if not CONTRACTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in CONTRACTS_DIR.glob("*.yaml"))


def contract(name: str) -> str | None:
    path = CONTRACTS_DIR / f"{Path(name).stem}.yaml"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def ontology_yaml() -> str | None:
    path = KNOWLEDGE_DIR / "ontology.yaml"
    return path.read_text(encoding="utf-8") if path.is_file() else None
