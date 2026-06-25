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


def test_committed_module_is_not_stale():
    """Drift guard: regenerating from knowledge/ must reproduce the committed module."""
    fresh = compile_okf.build()
    assert fresh["perimeter"] == _okf_context.ANALYST_PERIMETER
    assert fresh["join_bullets"] == _okf_context.ANALYST_JOIN_BULLETS
