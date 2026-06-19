"""Offline unit tests for the steward's planner/generator/evaluator.

Run with NO Postgres and NO API key — exercises the reasoning logic, same pattern
as the loan API's offline tests. The durable harness itself (DBOS) is verified
separately against Postgres (see README).
Run: cd products/steward/harness && python -m pytest -q
"""
from planner import make_plan
from generator import run_step
from evaluator import judge


def test_plan_is_nonempty_list():
    plan = make_plan("Reconcile yesterday's ledger and flag anomalies")
    assert isinstance(plan, list) and len(plan) >= 3


def test_routine_step_scores_high():
    result = run_step("Validate records against the active data contracts", history=[])
    assert "[UNCERTAIN]" not in result
    assert judge("Validate records", result)["score"] >= 0.6


def test_uncertain_step_triggers_escalation():
    result = run_step("Detect and flag reconciliation anomalies", history=[])
    assert "[UNCERTAIN]" in result
    assert judge("Detect anomalies", result)["score"] < 0.6  # -> harness escalates


def test_evaluator_shape():
    v = judge("x", "Completed x.")
    assert set(v) == {"score", "reason"} and 0.0 <= v["score"] <= 1.0
