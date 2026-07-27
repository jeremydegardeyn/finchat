"""OKF grounding: perimeter/join-model correctness + drift guard.

Ensures the committed ui/_okf_context.py still matches a fresh compile of the OKF
bundle, so the analyst semantic perimeter and the CA join model cannot silently
drift from knowledge/. Run: `pip install pyyaml && pytest test_okf_context.py`.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                       # ui/  -> _okf_context
sys.path.insert(0, str(HERE.parent / "scripts"))    # scripts/ -> compile_okf

import _okf_context  # noqa: E402
import compile_okf   # noqa: E402


def test_perimeter_matches_expected_surface():
    p = _okf_context.ANALYST_PERIMETER
    assert p["graph"][:3] == ["dim_customer", "dim_account", "fact_transaction"]
    assert "customer_360" in p["graph"] and "kg_relationships" in p["graph"]
    assert p["gold"] == ["overdraft_history"]
    assert p["loans"] == ["loan_status"]
    # No silver/bronze ever leaks into the analyst perimeter.
    flat = [t for tables in p.values() for t in tables]
    assert not any("silver" in t or "bronze" in t for t in flat)


def test_join_bullets_cover_the_three_hop():
    b = _okf_context.ANALYST_JOIN_BULLETS
    assert "fact_transaction.account_id = dim_account.account_id" in b
    assert "dim_account.customer_id = dim_customer.customer_id" in b


def test_knowledge_corpus_grounds_the_concept_docs():
    k = _okf_context.ANALYST_KNOWLEDGE
    # Every descriptive layer contributes at least one section header.
    for d in ("datasets/", "tables/", "views/", "metrics/", "graph/"):
        assert f"### {d}" in k
    # It carries semantics the raw schema can't (a metric name, the graph view).
    assert "### metrics/overdraft-ratio.md" in k and "### views/customer-360.md" in k
    # The playbooks contribute NO section (they compile to perimeter/joins, not corpus);
    # a doc may still *reference* a playbook by path in its prose, so match the header form.
    assert "### playbooks/" not in k and "### index.md" not in k


def test_glossary_is_compiled_with_synonyms():
    """Inc 22: business vocabulary reaches the agent, including the unmodelled terms it
    must decline rather than guess at."""
    g = {x["term"]: x for x in _okf_context.ANALYST_GLOSSARY}
    assert "Active Customer" in g and g["Active Customer"]["status"] == "certified"
    # "revenue" must resolve to the canonical metric, not be re-derived
    assert "revenue" in g["Net Revenue"]["synonyms"]
    assert "net_transaction_amount" in g["Net Revenue"]["maps_to"]
    # Household is deliberately NOT modelled — the agent must refuse, not compute
    assert g["Household"]["modelled"] is False
    assert sum(len(x["synonyms"]) for x in _okf_context.ANALYST_GLOSSARY) >= 10


def test_refusal_rules_reach_the_prompt():
    """The compiled bullets are what actually constrain the model at runtime."""
    b = _okf_context.ANALYST_REFUSAL_BULLETS
    assert b.count("\n") == len(_okf_context.ANALYST_REFUSALS["rules"])
    assert "masked" in b.lower() and "advice" in b.lower()


def test_committed_module_is_not_stale():
    """Drift guard: regenerating from knowledge/ must reproduce the committed module."""
    fresh = compile_okf.build()
    assert fresh["perimeter"] == _okf_context.ANALYST_PERIMETER
    assert fresh["join_bullets"] == _okf_context.ANALYST_JOIN_BULLETS
    assert fresh["concept_corpus"] == _okf_context.ANALYST_KNOWLEDGE
    assert fresh["glossary"] == _okf_context.ANALYST_GLOSSARY
    assert fresh["refusals"] == _okf_context.ANALYST_REFUSALS
    assert fresh["stewardship"] == _okf_context.CONCEPT_STEWARDSHIP
