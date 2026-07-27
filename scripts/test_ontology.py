"""Ontology drift guards: the committed artifacts must match a fresh projection
of knowledge/ontology.yaml. This is what makes the ontology a real SSOT — the OKF
grounding, the analyst perimeter, and the BigQuery kg_relationships view can no
longer silently diverge from the conceptual model.

Run: `pip install pyyaml && pytest scripts/test_ontology.py -q`
"""
import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import compile_ontology as onto  # noqa: E402
import compile_okf as okf        # noqa: E402  (glossary + refusal projections)

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


def test_code_sets_doc_is_in_sync():
    """Inc 22: knowledge/reference/code-sets.md is a projection of the ontology enums —
    regenerating must be a no-op, so an agent can never be told a stale code set."""
    text = onto.CODE_SETS_MD.read_text(encoding="utf-8")
    b, e = onto._region(text, onto._MD_BEGIN, onto._MD_END)
    assert text[b:e] == onto.render_code_sets_md(MODEL)


def test_every_class_has_an_owner_steward_and_valid_tier():
    """Accountability layer: an unowned concept is an unmaintainable one."""
    for name, s in onto.stewardship(MODEL).items():
        assert "@" in s["owner"], f"{name} has no owner"
        assert "@" in s["steward"], f"{name} has no steward"
        assert s["tier"] in ("certified", "curated", "raw"), f"{name} tier={s['tier']}"


def test_golden_queries_are_well_formed():
    """The golden set doubles as the eval set, so a malformed entry silently weakens
    coverage: routes must be real and every named table must be inside the perimeter."""
    gq = yaml.safe_load((onto.ROOT / "knowledge" / "golden-queries.yaml").read_text(encoding="utf-8"))
    per = {v for views in onto.perimeter(MODEL).values() for v in views}
    ids = set()
    for q in gq["queries"]:
        assert q["id"] not in ids, f"duplicate golden id {q['id']}"
        ids.add(q["id"])
        assert q["route"] in ("analytics", "kb", "semantics", "refuse"), q["id"]
        assert q.get("must"), f"{q['id']} has no expectations"
        for t in q.get("perimeter_tables", []):
            assert t in per, f"{q['id']} references out-of-perimeter table {t}"
    # every route is exercised, and refusals cite a real rule id
    assert {q["route"] for q in gq["queries"]} == {"analytics", "kb", "semantics", "refuse"}
    rules = {r["id"] for r in okf._refusals()["rules"]}
    for q in gq["queries"]:
        if q["route"] == "refuse":
            assert q.get("refusal") in rules, f"{q['id']} cites unknown refusal {q.get('refusal')}"


def test_glossary_terms_map_to_real_assets():
    """A glossary term pointing at a non-existent view or metric is worse than no term."""
    per = {v for views in onto.perimeter(MODEL).values() for v in views}
    known = per | set(MODEL["metrics"]) | {"account_balance"}
    for g in okf._glossary():
        if not g["modelled"]:
            assert not g["maps_to"], f"{g['term']} is not modelled but claims mappings"
            continue
        assert g["maps_to"], f"{g['term']} is modelled but maps to nothing"
        for target in g["maps_to"]:
            assert target in known, f"{g['term']} maps to unknown asset {target}"


def test_refusal_rules_are_complete():
    """Agent safety: each rule needs both a prohibition and a user-facing line."""
    r = okf._refusals()
    ids = {x["id"] for x in r["rules"]}
    # the non-negotiables for a read-only banking agent
    assert {"identity", "masked_null", "out_of_perimeter", "action"} <= ids
    for rule in r["rules"]:
        assert rule["rule"].strip() and rule["say"].strip(), rule["id"]
    assert r["escalate"], "no escalation triggers defined"


def test_graph_view_and_join_bullets_share_the_same_join_model():
    """Both projections come from the same relationships, so the (from,to,key) tuples
    must line up one-to-one."""
    rows = onto._rows(MODEL)
    sql = onto.kg_select_sql(MODEL)
    for r in rows:
        assert f"'{r['from_view']}'" in sql and f"'{r['to_view']}'" in sql
    assert len(rows) == onto.join_bullets(MODEL).count("\n")
