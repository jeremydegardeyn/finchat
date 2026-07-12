"""Ontology drift guards: the committed artifacts must match a fresh projection
of knowledge/ontology.yaml. This is what makes the ontology a real SSOT — the OKF
grounding, the analyst perimeter, and the BigQuery kg_relationships view can no
longer silently diverge from the conceptual model.

Run: `pip install pyyaml && pytest scripts/test_ontology.py -q`
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import compile_ontology as onto  # noqa: E402

MODEL = onto.load()


def test_perimeter_matches_ontology():
    p = onto.perimeter(MODEL)
    assert p["graph"] == ["dim_customer", "dim_account", "fact_transaction",
                          "customer_360", "kg_relationships"]
    assert p["gold"] == ["overdraft_history"]
    assert p["loans"] == ["loan_status"]


def test_perimeter_never_leaks_silver_or_bronze():
    flat = [v for views in onto.perimeter(MODEL).values() for v in views]
    assert not any("silver" in v or "bronze" in v for v in flat)


def test_join_bullets_reconcile_all_four_relationships():
    b = onto.join_bullets(MODEL)
    assert b.count("\n") == 4                     # 3 legacy + the reconciled ROLLS_UP
    assert "fact_transaction.account_id = dim_account.account_id" in b
    assert "dim_account.customer_id = dim_customer.customer_id" in b
    # customer_360 join was in the graph view but MISSING from the OKF bullets pre-Inc-20.
    assert "customer_360.customer_id = dim_customer.customer_id" in b


def test_graph_view_region_is_in_sync():
    """The committed kg_relationships region in graph.sql must equal a fresh render —
    i.e. `python scripts/compile_ontology.py` would be a no-op. This retires the
    hand-kept third copy of the join model."""
    text = onto.GRAPH_SQL.read_text(encoding="utf-8")
    b, e = onto._graph_region(text)
    assert text[b:e] == onto.render_graph_region(MODEL)


def test_classification_terms_exist_in_the_taxonomy():
    """Every `classification:` the ontology references must be a real term in the
    Terraform CLS taxonomy — the ontology cannot invent a sensitivity class."""
    used = {term for _, term in onto.ontology_classifications(MODEL)}
    assert used, "ontology declares no classifications — expected PII assignments"
    assert used <= onto.taxonomy_terms()


def test_ontology_classifications_match_deployed_policy_tags():
    """The CLS assignment the ontology owns must equal the policy tags actually
    deployed on the silver columns. This is the SSOT link for column-level security:
    the ontology and the Terraform can no longer disagree on what is sensitive."""
    assert onto.ontology_classifications(MODEL) == onto.deployed_column_tags()


def test_graph_view_and_join_bullets_share_the_same_join_model():
    """Both projections come from the same relationships, so the (from,to,key) tuples
    must line up one-to-one."""
    rows = onto._rows(MODEL)
    sql = onto.kg_select_sql(MODEL)
    for r in rows:
        assert f"'{r['from_view']}'" in sql and f"'{r['to_view']}'" in sql
    assert len(rows) == onto.join_bullets(MODEL).count("\n")
