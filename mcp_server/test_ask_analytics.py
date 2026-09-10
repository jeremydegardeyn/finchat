"""Tests for `ask_analytics` — the tool that finally consumes the identity plumbing.

Until this existed, `caller.require_staff` had no call site: the gate, the registry
lookup and the persona resolution were all tested and none of them ran in production.
So the case that matters most here is not the happy path, it is that the gate is
actually invoked and that a refusal is a refusal.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture()
def srv(monkeypatch):
    monkeypatch.setenv("FINCHAT_MCP_PERSONA", "customer")
    monkeypatch.syspath_prepend(str(HERE))
    spec = importlib.util.spec_from_file_location("server", HERE / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["server"] = module
    spec.loader.exec_module(module)
    return module


def _call(srv, question):
    return json.loads(srv.ask_analytics(question))


# --- the gate -----------------------------------------------------------------
def test_the_staff_gate_is_actually_called(srv, monkeypatch):
    """The whole point. A tool that forgets to call the gate looks identical in every
    other test — it answers, and it answers correctly. Only asserting the call catches
    a refactor that drops it."""
    called = []

    class _Stub:
        NotPermitted = RuntimeError

        @staticmethod
        def require_staff(tool):
            called.append(tool)

    monkeypatch.setattr(srv, "_caller", lambda: _Stub)
    _call(srv, "what does overdraft mean")
    assert called == ["ask_analytics"], "ask_analytics did not consult the staff gate"


def test_a_refusal_is_returned_not_raised(srv, monkeypatch):
    """A raised exception reaches the model as a transport error with no explanation.
    The refusal text is the only thing that tells the caller what to do instead, so it
    has to survive as content."""
    class _NotPermitted(Exception):
        pass

    class _Stub:
        NotPermitted = _NotPermitted

        @staticmethod
        def require_staff(tool):
            raise _NotPermitted("ask_analytics is a staff surface and this identity "
                                "is not provisioned for it.")

    monkeypatch.setattr(srv, "_caller", lambda: _Stub)
    out = _call(srv, "how many accounts went negative")
    assert "staff surface" in out["refused"]
    assert out["tool"] == "ask_analytics"


def test_it_still_works_when_the_caller_module_is_absent(srv, monkeypatch):
    """Over stdio there is no OAuth and no caller module. Refusing there would break
    `claude mcp add` without protecting the deployed surface."""
    monkeypatch.setattr(srv, "_caller", lambda: None)
    assert _call(srv, "what does overdraft mean")["mode"] == "semantics"


# --- routing ------------------------------------------------------------------
@pytest.fixture()
def open_gate(srv, monkeypatch):
    monkeypatch.setattr(srv, "_caller", lambda: None)
    return srv


def test_a_definitional_question_routes_to_the_data_model(open_gate):
    out = _call(open_gate, "what does overdraft mean here")
    assert out["mode"] == "semantics" and out["data_model"]


def test_a_policy_question_routes_to_the_knowledge_base(open_gate):
    out = _call(open_gate, "what is the fee for a returned item")
    assert out["mode"] == "kb"


def test_a_platform_question_routes_to_platform(open_gate):
    out = _call(open_gate, "why did we choose BigQuery, see the ADR")
    assert out["mode"] == "platform"


def test_an_empty_question_is_rejected_before_routing(open_gate):
    assert "error" in _call(open_gate, "   ")


# --- the honest refusal -------------------------------------------------------
def test_a_value_question_is_refused_with_its_reason_and_its_unblocker(open_gate):
    """This refusal is load-bearing documentation. Computing a number here would use the
    SERVICE's entitlements rather than the asker's, which over-reports silently — the
    worst failure shape available to an analytics tool."""
    out = _call(open_gate, "how many accounts went negative last month")
    assert out["mode"] == "analytics"
    assert "masking" in out["refused"] and "column-level security" in out["refused"]
    assert out["instead"], "a refusal must say what IS available"
    assert "ADR-0019" in out["unblocked_by"]


def test_the_refusal_never_invents_a_number(open_gate):
    """Belt and braces on the one thing that must never happen."""
    out = _call(open_gate, "what is the total balance across all accounts")
    assert "rows" not in out and "sql" not in out and "answer" not in out


# --- routing rules come from the process layer --------------------------------
def test_routing_uses_the_shared_owner_not_a_local_copy(open_gate):
    """Two channels disagreeing about which tool answers a question is exactly the drift
    ADR-0030 put this rule in the process layer to prevent."""
    import loader
    owner = loader.load("analyst_routing_check",
                        HERE.parent / "products" / "process" / "api" / "analyst_routing.py")
    for q in ("what is the fee for an overdraft",
              "how many customers have two accounts",
              "what does active customer mean",
              "which ADR covers the agent registry"):
        assert _call(open_gate, q)["mode"] == owner.heuristic_intent(q), q
