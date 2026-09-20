"""The safety classifier's model access (server._safety_transport), gateway leg only.

Pins what the gateway's classification profile changed on this side (2026-09-19): the
transport asks for the JSON profile, keeps the `(text, model)` contract `safety_signals.
classify` expects, and — when a verdict still comes back cut off — says where the budget
went while the payload that knows is in hand.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("fastapi")

import safety_signals as ss  # noqa: E402
import server  # noqa: E402


class _GW:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.calls = payload, exc, []

    def complete(self, prompt, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.payload


def test_transport_asks_for_the_json_profile_and_returns_text_and_served_model(monkeypatch):
    gw = _GW({"outcome": "ok", "text": '{"signals":{}}', "model": "gemini-2.5-flash",
              "model_served": "gemini-2.5-flash-001", "finish_reason": "STOP",
              "output_tokens": 110, "thoughts_tokens": 0, "profile": "json"})
    monkeypatch.setattr(server, "_gw", gw)
    out = server._safety_transport("prompt", 512)
    assert out == ('{"signals":{}}', "gemini-2.5-flash-001")
    call = gw.calls[0]
    assert call["response_format"] == "json"
    assert call["workload_class"] == "classification"
    assert call["max_output_tokens"] == 512


def test_truncated_verdict_logs_where_the_budget_went(monkeypatch, capsys):
    """The live failure shape: 7 tokens of JSON, 249 of reasoning. `classify` records
    parse:truncated from the text alone; only the transport can say it was reasoning."""
    gw = _GW({"outcome": "ok", "text": '{"signals":{"jailbreak_', "model": "gemini-2.5-flash",
              "finish_reason": "MAX_TOKENS", "output_tokens": 7, "thoughts_tokens": 249,
              "profile": None})
    monkeypatch.setattr(server, "_gw", gw)
    text, model = server._safety_transport("prompt", 256)
    assert ss.parse_verdict(text) is None            # what classify will see
    assert model == "gemini-2.5-flash"               # served missing -> requested
    log = capsys.readouterr().out
    assert "truncated at 256" in log and "thoughts_tokens=249" in log and "output_tokens=7" in log


def test_clean_verdict_logs_nothing(monkeypatch, capsys):
    gw = _GW({"outcome": "ok", "text": "{}", "model": "m", "finish_reason": "STOP"})
    monkeypatch.setattr(server, "_gw", gw)
    server._safety_transport("prompt", 512)
    assert "truncated" not in capsys.readouterr().out


def test_gateway_refusal_is_no_verdict_not_a_vertex_retry(monkeypatch):
    """Unchanged contract, re-pinned because the call moved to `_gw_payload`: a policy
    refusal must surface as ClassifierUnavailable, never fall through to Vertex."""
    gw = _GW(exc=RuntimeError("pii_blocked"))
    monkeypatch.setattr(server, "_gw", gw)
    with pytest.raises(ss.ClassifierUnavailable, match="gateway:pii_blocked"):
        server._safety_transport("prompt", 512)


def test_cap_is_sized_for_the_answer_not_the_mitigation():
    """512 covers a ~120-token verdict four times over. 2048 was the thinking-model
    mitigation; going back up would hide a regression in the gateway profile."""
    assert ss.CLASSIFIER_MAX_TOKENS == 512
