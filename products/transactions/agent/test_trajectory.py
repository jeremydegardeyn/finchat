"""Tests for the /chat trajectory surface (trajectory.py).

Two things are being held in place here.

First, the default turn. The customer path proxies /chat's body through the BFF to the
browser verbatim, so the tool trace must not appear unless a caller asked for it. A leak
here would put account ids and raw tool output in a customer reply, and it would be
invisible in review because the field is additive.

Second, the ADR-0022 evidence half. `scripts/canary_eval.py` has always read a serving
version off /chat and always received None, because ADK's `LlmResponse.create()` drops
`modelVersion` and nothing carried it. The contract that fixes it — gateway_llm writes the
version into `custom_metadata`, ADK merges that into the Event, /chat reads it back — spans
someone else's framework, so the assumption is asserted rather than assumed.

Fakes rather than a Runner on purpose: this file must run in CI, which installs pytest and
not ADK.
"""
from __future__ import annotations

import json

import pytest

from trajectory import TurnTrace


# --------------------------------------------------------------------------- #
# Minimal stand-ins for the ADK/genai objects TurnTrace reads.
# --------------------------------------------------------------------------- #
class FakeCall:
    def __init__(self, name, args):
        self.name, self.args = name, args


class FakeResponse:
    def __init__(self, name, response):
        self.name, self.response = name, response


class FakePart:
    def __init__(self, *, text=None, function_call=None, function_response=None):
        self.text = text
        self.function_call = function_call
        self.function_response = function_response


class FakeContent:
    def __init__(self, parts):
        self.parts = parts

    def model_dump(self, **_kw):
        out = []
        for p in self.parts:
            if p.function_call is not None:
                out.append({"functionCall": {"name": p.function_call.name,
                                             "args": p.function_call.args}})
            elif p.function_response is not None:
                out.append({"functionResponse": {"name": p.function_response.name,
                                                 "response": p.function_response.response}})
            else:
                out.append({"text": p.text})
        return {"parts": out}


class FakeEvent:
    def __init__(self, parts, *, author="agent", final=False, meta=None):
        self.content = FakeContent(parts) if parts is not None else None
        self.author = author
        self.custom_metadata = meta
        self._final = final

    def is_final_response(self):
        return self._final


def a_turn():
    """The shape of a real tool-calling turn: call, result, then the answer."""
    meta = {"finchat": {"model_requested": "gemini-2.5-flash",
                        "model_served": "gemini-2.5-flash-002",
                        "path": "gateway"}}
    return [
        FakeEvent([FakePart(function_call=FakeCall("get_account_balance",
                                                  {"account_id": "acct-001"}))],
                  meta=meta),
        FakeEvent([FakePart(function_response=FakeResponse(
            "get_account_balance", {"balance": 1234.56, "currency": "USD"}))],
            author="user"),
        FakeEvent([FakePart(text="Your balance on acct-001 is 1234.56 USD.")],
                  final=True, meta=meta),
    ]


def fold(events, **kw):
    t = TurnTrace(**kw)
    for e in events:
        t.add(e)
    return t


# --------------------------------------------------------------------------- #
# The default turn stays clean
# --------------------------------------------------------------------------- #
def test_default_turn_exposes_no_trace():
    body = fold(a_turn()).payload("s1")
    assert body["response"] == "Your balance on acct-001 is 1234.56 USD."
    for leaked in ("trajectory", "tool_outputs", "intermediate_events", "gateway_path"):
        assert leaked not in body, f"{leaked} must not appear without include_trajectory"


def test_default_turn_body_is_json_serializable():
    # The BFF proxies this verbatim; an unserializable value would 500 the customer turn.
    json.dumps(fold(a_turn()).payload("s1"))


# --------------------------------------------------------------------------- #
# The evaluation surface
# --------------------------------------------------------------------------- #
def test_trajectory_records_calls_in_order():
    body = fold(a_turn(), collect_trajectory=True).payload("s1")
    assert body["trajectory"] == [
        {"name": "get_account_balance", "arguments": {"account_id": "acct-001"}}]


def test_tool_outputs_carry_the_grounding_evidence():
    body = fold(a_turn(), collect_trajectory=True).payload("s1")
    assert body["tool_outputs"] == [
        {"name": "get_account_balance",
         "response": {"balance": 1234.56, "currency": "USD"}}]


def test_intermediate_events_keep_every_content_event():
    body = fold(a_turn(), collect_trajectory=True).payload("s1")
    assert len(body["intermediate_events"]) == 3
    assert body["intermediate_events"][0]["author"] == "agent"
    assert "functionCall" in body["intermediate_events"][0]["content"]["parts"][0]


def test_a_refused_turn_has_an_empty_trajectory():
    # Not a missing measurement — an empty trajectory is how "declined without reading
    # the customer's data" is scored (eval/pipelines/vertex_eval.py::expected_calls).
    events = [FakeEvent([FakePart(text="I can't provide investment advice.")], final=True)]
    body = fold(events, collect_trajectory=True).payload("s1")
    assert body["trajectory"] == []
    assert body["tool_outputs"] == []


def test_trajectory_frame_is_json_serializable():
    json.dumps(fold(a_turn(), collect_trajectory=True).payload("s1"))


# --------------------------------------------------------------------------- #
# ADR-0022: the serving version
# --------------------------------------------------------------------------- #
def test_serving_version_is_reported():
    body = fold(a_turn()).payload("s1")
    assert body["model_requested"] == "gemini-2.5-flash"
    assert body["model_served"] == "gemini-2.5-flash-002"


def test_unreported_version_stays_none_rather_than_the_requested_id():
    """The whole point of the control. An assumption recorded as a fact is worse than a
    gap, because a gap is visible (scripts/model_pins.py)."""
    meta = {"finchat": {"model_requested": "gemini-2.5-flash", "model_served": None,
                        "path": "direct"}}
    events = [FakeEvent([FakePart(text="hi")], final=True, meta=meta)]
    body = fold(events).payload("s1")
    assert body["model_requested"] == "gemini-2.5-flash"
    assert body["model_served"] is None


def test_a_later_event_without_metadata_does_not_erase_the_version():
    events = a_turn() + [FakeEvent([FakePart(text="trailing")], meta=None)]
    assert fold(events).model_served == "gemini-2.5-flash-002"


def test_events_without_content_are_tolerated():
    events = [FakeEvent(None)] + a_turn()
    assert fold(events, collect_trajectory=True).payload("s1")["trajectory"]


# --------------------------------------------------------------------------- #
# The cross-framework assumption
# --------------------------------------------------------------------------- #
def test_adk_event_still_carries_custom_metadata():
    """gateway_llm sets `custom_metadata` on the LlmResponse; ADK's
    `_finalize_model_response_event` merges that dump into the Event. If a version bump
    renames or drops the field, the version reporting goes silently back to None — which
    is precisely the failure this increment fixed, so it gets an assertion rather than a
    comment."""
    adk = pytest.importorskip("google.adk.events",
                              reason="ADK not installed (CI installs pytest only)")
    assert "custom_metadata" in adk.Event.model_fields
    llm = pytest.importorskip("google.adk.models")
    assert "custom_metadata" in llm.LlmResponse.model_fields
