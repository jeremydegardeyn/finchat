"""Tests for the governed-path client (ADR-0024).

The behaviours worth pinning down are the failure modes, not the happy path: a policy
refusal must never fall back to a direct model call, and every bypass must be counted.
"""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gateway_client as gc  # noqa: E402


@pytest.fixture(autouse=True)
def reset():
    gc._counters.update({"transited": 0, "bypass_unconfigured": 0,
                         "bypass_error": 0, "blocked": 0})
    yield


class _Resp(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(monkeypatch, payload=None, exc=None):
    monkeypatch.setattr(gc, "GATEWAY_URL", "https://gw.example")
    monkeypatch.setattr(gc, "_id_token", lambda a: None)

    def fake(req, timeout=None):
        if exc:
            raise exc
        return _Resp(json.dumps(payload).encode())

    monkeypatch.setattr(gc.urllib.request, "urlopen", fake)


def _call():
    return gc.complete("hi", agent_id="a", workload_class="classification")


# --- Bypass accounting --------------------------------------------------------

def test_unconfigured_gateway_falls_back_and_is_counted(monkeypatch):
    monkeypatch.setattr(gc, "GATEWAY_URL", "")
    assert _call() is None
    assert gc.counters()["bypass_unconfigured"] == 1
    assert gc.counters()["transit_share"] == 0.0


def test_unreachable_gateway_falls_back_and_is_counted(monkeypatch):
    _stub(monkeypatch, exc=OSError("connection refused"))
    assert _call() is None
    assert gc.counters()["bypass_error"] == 1


def test_success_is_counted_as_transited(monkeypatch):
    _stub(monkeypatch, payload={"outcome": "ok", "text": "hello",
                                "model": "m", "model_served": "m-001"})
    r = _call()
    assert r["text"] == "hello" and r["model_served"] == "m-001"
    assert gc.counters()["transited"] == 1
    assert gc.counters()["transit_share"] == 1.0


def test_transit_share_mixes_correctly(monkeypatch):
    _stub(monkeypatch, payload={"outcome": "ok", "text": "x"})
    _call()
    _call()
    _call()
    monkeypatch.setattr(gc, "GATEWAY_URL", "")
    _call()
    assert gc.counters()["transit_share"] == 0.75


# --- The failure mode a gateway must not have ---------------------------------

@pytest.mark.parametrize("outcome", ["pii_blocked", "budget_exceeded",
                                     "unregistered_workload"])
def test_policy_refusal_raises_and_never_falls_back(monkeypatch, outcome):
    """A refusal is the control working. Returning None here would let the caller
    retry directly against Vertex, routing around the control that just fired."""
    _stub(monkeypatch, payload={"outcome": outcome, "error": "no"})
    with pytest.raises(gc.GatewayBlocked) as e:
        _call()
    assert e.value.outcome == outcome
    assert gc.counters()["blocked"] == 1
    assert gc.counters()["bypass_error"] == 0  # not counted as availability


def test_model_error_is_availability_not_policy(monkeypatch):
    """The gateway reached a decision but couldn't serve — fall back, and count it."""
    _stub(monkeypatch, payload={"outcome": "model_error", "error": "Timeout"})
    assert _call() is None
    assert gc.counters()["bypass_error"] == 1
    assert gc.counters()["blocked"] == 0


def test_unrecognised_outcome_is_treated_as_availability(monkeypatch):
    _stub(monkeypatch, payload={"outcome": "something_new"})
    assert _call() is None
    assert gc.counters()["bypass_error"] == 1


def test_malformed_response_does_not_crash_the_caller(monkeypatch):
    _stub(monkeypatch, exc=ValueError("not json"))
    assert _call() is None
    assert gc.counters()["bypass_error"] == 1


def test_counters_report_configured_state(monkeypatch):
    monkeypatch.setattr(gc, "GATEWAY_URL", "")
    assert gc.counters()["configured"] is False
    monkeypatch.setattr(gc, "GATEWAY_URL", "https://gw.example")
    assert gc.counters()["configured"] is True


def test_transit_share_is_none_before_any_call():
    assert gc.counters()["transit_share"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
