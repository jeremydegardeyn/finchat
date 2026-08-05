"""Tests for the canary drift control (ADR-0022).

As with the registry gate, the point is to prove the control fires — and, just as
importantly, that it does *not* fire on noise. A canary that cries wolf gets muted, and a
muted control is worse than none.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import canary_eval as c  # noqa: E402
import model_pins as mp  # noqa: E402


def _run(grounding=1.0, tool=1.0, halluc=0.0, served="v1"):
    return {"model_served": served,
            "metrics": {"grounding_accuracy": grounding, "tool_utilization": tool,
                        "hallucination_rate": halluc}}


# --- Regression detection -----------------------------------------------------

def test_no_regression_when_metrics_hold():
    assert c.compare(_run(), _run()) == []


def test_small_wobble_does_not_fire():
    """Within tolerance must stay silent, or the control gets muted in practice."""
    assert c.compare(_run(grounding=0.95), _run(grounding=1.0)) == []


def test_grounding_drop_beyond_tolerance_fires():
    regs = c.compare(_run(grounding=0.80), _run(grounding=1.0))
    assert any("grounding_accuracy" in r for r in regs)


def test_hallucination_is_inverted():
    """A RISE in hallucination is the regression, not a fall."""
    assert c.compare(_run(halluc=0.20), _run(halluc=0.0)) != []
    assert c.compare(_run(halluc=0.0), _run(halluc=0.20)) == []


def test_missing_metric_is_skipped_not_failed():
    base = {"metrics": {}}
    assert c.compare(_run(), base) == []


# --- Version attribution ------------------------------------------------------

def test_version_change_is_detected():
    assert c.version_changed(_run(served="v2"), _run(served="v1")) == "v1 -> v2"


def test_unknown_version_is_not_reported_as_a_change():
    """An unreported version must not masquerade as a version change."""
    assert c.version_changed(_run(served=None), _run(served="v1")) is None
    assert c.version_changed(_run(served="v1"), _run(served=None)) is None


# --- Scoring ------------------------------------------------------------------

def test_refusal_case_scores_correctly():
    case = {"id": "adv-1", "expected_tool": None}
    assert c.score(case, "I can't provide investment advice.")["grounded"] is True
    assert c.score(case, "Sure, buy tech stocks.")["hallucinated"] is True


def test_empty_answer_counts_as_hallucination_when_grounding_expected():
    case = {"id": "bal-1", "expected_tool": "get_account_balance",
            "account_id": "acct-001", "expected_grounded": True}
    assert c.score(case, "")["hallucinated"] is True


def test_grounded_answer_passes():
    case = {"id": "bal-1", "expected_tool": "get_account_balance",
            "account_id": "acct-001", "expected_grounded": True}
    s = c.score(case, "Your balance on acct-001 is 1,240.55 USD.")
    assert s["grounded"] and s["tool_ok"] and not s["hallucinated"]


def test_dataset_loads_and_aggregates():
    cases = c.load_dataset()
    assert cases, "golden set is empty"
    agg = c.aggregate([c.score(x, "acct-001 answer") for x in cases])
    assert agg["n"] == len(cases)
    assert 0.0 <= agg["grounding_accuracy"] <= 1.0


# --- Pins ---------------------------------------------------------------------

def test_pin_overrides_alias(monkeypatch):
    monkeypatch.setenv("FINCHAT_PIN_JUDGE", "snapshot-123")
    assert mp.model_for("JUDGE") == "snapshot-123"
    assert mp.is_pinned("JUDGE")


def test_alias_used_when_unpinned(monkeypatch):
    monkeypatch.delenv("FINCHAT_PIN_ROUTER", raising=False)
    assert mp.model_for("ROUTER") == mp.ALIASES["ROUTER"]
    assert not mp.is_pinned("ROUTER")


def test_unknown_call_site_raises():
    with pytest.raises(KeyError):
        mp.model_for("NOPE")


def test_served_version_extraction():
    assert mp.served_version({"modelVersion": "gemini-2.5-flash-001"}) == "gemini-2.5-flash-001"


def test_served_version_never_guesses():
    """Absent means unknown. Back-filling the requested version would make the
    pinning evidence a tautology."""
    assert mp.served_version({}) is None
    assert mp.served_version({"modelVersion": ""}) is None
    assert mp.served_version(None) is None


def test_every_call_site_has_a_workload_class():
    assert set(mp.ALIASES) == set(mp.WORKLOAD_CLASS)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
