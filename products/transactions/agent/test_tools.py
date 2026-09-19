"""Account tools: a 404 is an answer, and a missing fallback is not a crash.

Observed in dev on 2026-09-19: `get_account_balance("acct-001")` got a 404 from txn-api
(the dev dataset has no such account), `raise_for_status` threw, the except path did
`from bq import Repository`, and bq.py is not in the agent image. Result: a
ModuleNotFoundError inside a tool, a 500 on POST /chat, and a UI that hid it behind
its demo fallback. Neither step should have raised.
"""
from __future__ import annotations

import sys
import types

import pytest

import tools


class _Resp:
    def __init__(self, status: int, body=None):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def _serve(monkeypatch, status: int, body=None):
    """Stub the transport so the test needs neither httpx nor a running API."""
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        return _Resp(status, body)

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(get=get))
    monkeypatch.setattr(tools, "API_BASE", "http://txn-api.test")
    return calls


def _unreachable(monkeypatch):
    def get(url, headers=None, timeout=None):
        raise ConnectionError("refused")
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(get=get))
    monkeypatch.setattr(tools, "API_BASE", "http://txn-api.test")


# --- 404: the API's answer is returned, the fallback is never consulted ----------

def test_balance_404_returns_not_found_without_touching_fallback(monkeypatch):
    _serve(monkeypatch, 404, {"detail": "account acct-999 not found"})
    monkeypatch.setattr(tools, "_fallback_repo",
                       lambda: pytest.fail("404 must not reach the fallback"))
    out = tools.get_account_balance("acct-999")
    assert out == {"error": "account acct-999 not found"}


def test_summary_404_returns_not_found(monkeypatch):
    _serve(monkeypatch, 404)
    monkeypatch.setattr(tools, "_fallback_repo",
                       lambda: pytest.fail("404 must not reach the fallback"))
    assert tools.get_account_summary("acct-999") == {"error": "account acct-999 not found"}


def test_history_404_is_an_empty_list(monkeypatch):
    _serve(monkeypatch, 404)
    monkeypatch.setattr(tools, "_fallback_repo",
                       lambda: pytest.fail("404 must not reach the fallback"))
    assert tools.get_transaction_history("acct-999") == []


def test_200_passes_the_body_through(monkeypatch):
    body = {"account_id": "acct-001", "currency": "USD", "balance": 12.5, "last_activity_at": None}
    calls = _serve(monkeypatch, 200, body)
    assert tools.get_account_balance("acct-001") == body
    assert calls == ["http://txn-api.test/v1/accounts/acct-001/balance"]


# --- unreachable API: fallback if shipped, an error dict if not -------------------

def test_unreachable_api_without_bq_returns_unavailable(monkeypatch):
    """The Cloud Run image has no bq.py. `None` in sys.modules makes `import bq` raise
    ImportError, which is exactly what the image does."""
    _unreachable(monkeypatch)
    monkeypatch.setitem(sys.modules, "bq", None)
    assert tools.get_account_balance("acct-001") == {"error": "account service unavailable"}
    assert tools.get_account_summary("acct-001") == {"error": "account service unavailable"}
    assert tools.get_transaction_history("acct-001") == [{"error": "account service unavailable"}]


def test_unreachable_api_with_bq_serves_demo_data(monkeypatch):
    """The offline eval (eval/pipelines/evaluate.py) runs from a source checkout and
    relies on this path; the guard above must not have cost it."""
    _unreachable(monkeypatch)
    monkeypatch.delitem(sys.modules, "bq", raising=False)
    out = tools.get_account_balance("acct-001")
    assert out["account_id"] == "acct-001" and "balance" in out
    assert "error" in tools.get_account_balance("acct-999")
    assert tools.get_transaction_history("acct-999") == []


def test_non_404_http_error_still_degrades(monkeypatch):
    """A 503 from the API is an outage, not an answer: it takes the fallback path."""
    _serve(monkeypatch, 503)
    monkeypatch.setitem(sys.modules, "bq", None)
    assert tools.get_account_balance("acct-001") == {"error": "account service unavailable"}
