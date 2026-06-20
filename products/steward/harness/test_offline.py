"""Offline unit tests for the steward's planner/generator/evaluator.

Run with NO GCP_PROJECT and NO BigQuery — exercises the reasoning + check-routing
logic with the offline stub (run_dq_check returns healthy). The real BigQuery checks
and Vertex calls are covered by the live deploy, not unit tests.
Run: cd products/steward/harness && python -m pytest -q
"""
from planner import make_plan
from generator import run_step
from evaluator import judge


def test_plan_lists_products_plus_summary():
    plan = make_plan("Reconcile yesterday's ledger and flag anomalies")
    assert isinstance(plan, list) and len(plan) >= 3
    assert plan[-1].lower().startswith("summarize")
    assert any("(" in t and ")" in t for t in plan)  # tasks carry a (dataset.table)


def test_reconcile_step_offline_is_ok():
    # Offline stub treats the table as healthy -> "OK ...", scores high.
    result = run_step("Reconcile deposit-transactions (finchat_silver.transaction)", history=[])
    assert result.startswith("OK")
    assert judge("reconcile", result)["score"] >= 0.6


def test_violation_escalates():
    result = "[VIOLATION] finchat_gold.overdraft_history: 0 rows — EMPTY (contract violation)"
    assert judge("reconcile", result)["score"] < 0.6  # -> harness escalates to approver


def test_summary_step_rolls_up_violations():
    history = [{"task": "Reconcile x (d.t)", "result": "[VIOLATION] d.t: 0 rows — EMPTY"}]
    out = run_step("Summarize reconciliation findings", history)
    assert "violation" in out.lower()


def test_evaluator_shape():
    v = judge("x", "OK d.t: 10 rows")
    assert set(v) == {"score", "reason"} and 0.0 <= v["score"] <= 1.0
