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


# --- enforced chokepoint (ADR-0024 / ADR-0026) --------------------------------
# Counting a bypass tells you the control was skipped; it does not stop the skipping.
# With AI_GATEWAY_REQUIRED set there is no direct-to-Vertex path at all, so the screening
# the gateway performs cannot be routed around by unsetting an environment variable.

def test_unconfigured_gateway_still_degrades_by_default(monkeypatch):
    """Default posture is unchanged: a sandbox keeps working when the gateway is absent."""
    monkeypatch.setattr(gc, "GATEWAY_URL", "")
    monkeypatch.setattr(gc, "GATEWAY_REQUIRED", False)
    assert gc.complete("hi", agent_id="a", workload_class="w") is None


def test_unconfigured_gateway_fails_closed_when_required(monkeypatch):
    monkeypatch.setattr(gc, "GATEWAY_URL", "")
    monkeypatch.setattr(gc, "GATEWAY_REQUIRED", True)
    with pytest.raises(gc.GatewayUnavailable):
        gc.complete("hi", agent_id="a", workload_class="w")


def test_unreachable_gateway_fails_closed_when_required(monkeypatch):
    """An outage must not silently become an ungoverned call on a regulated path."""
    monkeypatch.setattr(gc, "GATEWAY_URL", "https://gw.invalid")
    monkeypatch.setattr(gc, "GATEWAY_REQUIRED", True)
    monkeypatch.setattr(gc, "_id_token", lambda a: None)

    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(gc.urllib.request, "urlopen", boom)

    with pytest.raises(gc.GatewayUnavailable):
        gc.complete("hi", agent_id="a", workload_class="w")


def test_a_refusal_is_still_a_refusal_not_an_outage(monkeypatch):
    """GatewayBlocked and GatewayUnavailable must stay distinct. A PII block is the control
    working; collapsing it into the outage path would let it be retried around."""
    assert not issubclass(gc.GatewayBlocked, gc.GatewayUnavailable)
    assert not issubclass(gc.GatewayUnavailable, gc.GatewayBlocked)


def test_bypasses_are_still_counted_when_required(monkeypatch):
    """Failing closed must not cost the transit-share measurement."""
    monkeypatch.setattr(gc, "GATEWAY_URL", "")
    monkeypatch.setattr(gc, "GATEWAY_REQUIRED", True)
    before = gc.counters()["bypass_unconfigured"]
    with pytest.raises(gc.GatewayUnavailable):
        gc.complete("hi", agent_id="a", workload_class="w")
    assert gc.counters()["bypass_unconfigured"] == before + 1
