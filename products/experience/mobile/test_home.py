"""Offline tests for the mobile experience API. No GCP, no network.

The interesting assertions are about what this layer must NOT do. Anything it decides is
a decision the web channel does not share, and that divergence is the failure the layer
was introduced to prevent.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_module(name, filename):
    """Import this service's module under a unique name.

    Every service in this repo names its app module `main.py`, so `import main` in a
    whole-repo pytest run resolves to whichever service was imported first. CI runs each
    suite with its own working-directory and would never see it; running the suite from
    the repo root does. Loading by path keeps the module addressable by where it lives.
    """
    import importlib.util
    import sys

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


main = _load_module("finchat_mobile_main", "main.py")

OVERVIEW = {
    "account_id": "acct-001",
    "currency": "USD",
    "balance": -2972.49,
    "last_activity_at": "2026-09-04T17:45:25+00:00",
    "recent_activity": [
        {"transaction_id": f"t{i}", "txn_type": "FEE", "amount": 10.0 + i,
         "currency": "USD", "status": "POSTED", "event_time": "2026-09-01T00:00:00Z"}
        for i in range(6)
    ],
    "loans": [{"loan_id": "l1", "status": "PENDING_APPROVAL", "amount": 15000}],
    "next_action": {"kind": "await_loan_decision",
                    "label": "Your loan application is with an approver",
                    "reason": "loan l1 is PENDING_APPROVAL"},
    "partial": [],
}


def _home(monkeypatch, overview=None):
    monkeypatch.setattr(main, "_overview", lambda a: overview or OVERVIEW)
    return main.home(account_id="acct-001")


def test_the_screen_is_one_round_trip(monkeypatch):
    """The measurable claim for this layer, pinned so a refactor cannot quietly undo it."""
    calls = []
    monkeypatch.setattr(main, "_overview", lambda a: calls.append(a) or OVERVIEW)
    main.home(account_id="acct-001")
    assert len(calls) == 1


def test_the_headline_comes_from_the_process_layer(monkeypatch):
    """This channel renders the decision; it does not make it."""
    out = _home(monkeypatch)
    assert out["headline"] == OVERVIEW["next_action"]["label"]
    assert out["headline_kind"] == "await_loan_decision"


def test_activity_is_trimmed_to_what_the_screen_draws(monkeypatch):
    out = _home(monkeypatch)
    assert len(out["activity"]) == main.ACTIVITY_ON_SCREEN < len(OVERVIEW["recent_activity"])


def test_amounts_are_formatted_here_because_formatting_is_a_channel_concern(monkeypatch):
    out = _home(monkeypatch)
    assert out["balance"]["display"] == "-2,972.49 USD"


def test_a_masked_balance_reaches_the_screen_as_masked_not_as_zero(monkeypatch):
    """The one formatting bug that would matter.

    Rendering a policy-masked NULL as 0.00 shows the customer a number they might act
    on, which is worse than showing nothing (ADR-0019).
    """
    out = _home(monkeypatch, {**OVERVIEW, "balance": None,
                              "next_action": {"kind": "none", "label": "ok", "reason": "r"}})
    assert out["balance"]["display"] is None
    assert out["balance"]["masked"] is True
    assert out["balance"]["amount"] is None


def test_a_missing_section_is_named_rather_than_silently_dropped(monkeypatch):
    out = _home(monkeypatch, {**OVERVIEW, "partial": ["loans"]})
    assert out["unavailable"] == ["loans"]


def test_an_unconfigured_process_api_fails_loudly(monkeypatch):
    """No fallback to a system API. The absence of one is the layering."""
    monkeypatch.setattr(main, "PROCESS_API_URL", "")
    with pytest.raises(HTTPException) as e:
        main.home(account_id="acct-001")
    assert e.value.status_code == 503


def test_this_module_decides_nothing_about_the_customer():
    """A structural check, because the drift would be gradual and each step defensible.

    If the words that name a business judgement appear here, the rule has been copied
    out of the process layer and the two channels can now disagree.
    """
    src = open(os.path.join(os.path.dirname(__file__), "main.py"), encoding="utf-8").read()
    body = src[src.index("def home("):]
    for token in ("PENDING_APPROVAL", "APPROVED", "REJECTED", "< 0", "overdraft"):
        assert token not in body, (
            f"{token!r} appears in the mobile channel: business rules belong to the "
            "process API, or the web and mobile views will drift apart (ADR-0030).")
