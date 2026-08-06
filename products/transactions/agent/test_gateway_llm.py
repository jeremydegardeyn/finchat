"""Tests for the ADK gateway adapter (ADR-0024).

The thing worth proving is that a **tool-calling turn survives the round trip**. That is
the entire reason this adapter exists instead of an HTTP client: a prompt string cannot
carry function declarations, and an adapter that silently dropped them would look like it
worked right up until an agent stopped calling its tools.
"""
from __future__ import annotations

import asyncio
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gateway_llm as gl  # noqa: E402

pytestmark = pytest.mark.skipif(not gl._ADK_AVAILABLE, reason="ADK not installed")


@pytest.fixture(autouse=True)
def reset():
    gl._counters.update({"transited": 0, "bypass_error": 0, "blocked": 0})
    yield


def _request(with_tools=True, with_fn_turn=False):
    from google.adk.models import LlmRequest
    from google.genai import types

    contents = [types.Content(role="user", parts=[types.Part(text="balance on acct-001?")])]
    if with_fn_turn:
        contents += [
            types.Content(role="model", parts=[types.Part(
                function_call=types.FunctionCall(name="get_account_balance",
                                                 args={"account_id": "acct-001"}))]),
            types.Content(role="user", parts=[types.Part(
                function_response=types.FunctionResponse(
                    name="get_account_balance", response={"balance": 1240.55}))]),
        ]
    tools = None
    if with_tools:
        tools = [types.Tool(function_declarations=[types.FunctionDeclaration(
            name="get_account_balance", description="Balance for an account",
            parameters=types.Schema(type="OBJECT", properties={
                "account_id": types.Schema(type="STRING")}))])]
    cfg = types.GenerateContentConfig(system_instruction="You are FinChat.",
                                      temperature=0.1, tools=tools)
    return LlmRequest(model="gemini-2.5-flash", contents=contents, config=cfg)


# --- Serialization: the part that silently breaks agents ----------------------

def test_function_declarations_survive_serialization():
    body = gl._build_body(_request())
    assert "tools" in body, "tools must be top-level, not inside generationConfig"
    decls = body["tools"][0]["functionDeclarations"]
    assert decls[0]["name"] == "get_account_balance"


def test_camel_case_aliases_are_used():
    """google-genai types are snake_case in Python and camelCase on the wire. Sending
    the snake_case form yields a confusing 400 rather than an obvious one."""
    raw = json.dumps(gl._build_body(_request()))
    assert "functionDeclarations" in raw and "function_declarations" not in raw
    assert "systemInstruction" in raw and "system_instruction" not in raw


def test_system_instruction_is_not_left_in_generation_config():
    body = gl._build_body(_request())
    assert "systemInstruction" in body
    assert "systemInstruction" not in body.get("generationConfig", {})
    assert body["generationConfig"]["temperature"] == pytest.approx(0.1)


def test_function_call_and_response_turns_are_preserved():
    body = gl._build_body(_request(with_fn_turn=True))
    assert len(body["contents"]) == 3
    assert body["contents"][1]["parts"][0]["functionCall"]["name"] == "get_account_balance"
    assert body["contents"][2]["parts"][0]["functionResponse"]["response"]["balance"] == 1240.55


def test_body_is_json_serializable_with_binary_content():
    """The bug that reached production. pydantic's default (python) mode leaves bytes as
    bytes, so json.dumps raised TypeError at request time and killed the agent turn.
    Every fixture here used plain text, so nothing caught it. mode="json" fixes it."""
    from google.adk.models import LlmRequest
    from google.genai import types
    req = LlmRequest(model="gemini-2.5-flash", contents=[
        types.Content(role="user", parts=[
            types.Part(text="what are the overdraft fees?"),
            types.Part(inline_data=types.Blob(mime_type="application/pdf",
                                              data=b"PDF-binary-bytes")),
        ])])
    json.dumps(gl._build_body(req))  # must not raise


def test_enums_in_the_config_are_serializable():
    """Same failure class: a Schema type enum is not a str until mode="json"."""
    from google.adk.models import LlmRequest
    from google.genai import types
    cfg = types.GenerateContentConfig(tools=[types.Tool(
        function_declarations=[types.FunctionDeclaration(
            name="search_knowledge_base", description="KB search",
            parameters=types.Schema(type=types.Type.OBJECT, properties={
                "query": types.Schema(type=types.Type.STRING)}))])])
    req = LlmRequest(model="m", contents=[
        types.Content(role="user", parts=[types.Part(text="hi")])], config=cfg)
    json.dumps(gl._build_body(req))


def test_unserializable_payload_falls_back_instead_of_killing_the_turn(monkeypatch):
    """Defence in depth: even if a future field slips through, the agent must degrade to
    the direct path rather than raise. Serialization used to sit outside the try."""
    monkeypatch.setattr(gl, "GATEWAY_URL", "https://gw.example")
    monkeypatch.setattr(gl, "_id_token", lambda a: None)
    assert gl._post({"body": {"bad": b"raw bytes"}}) is None


def test_system_instruction_is_a_content_object_not_a_string():
    """ADK holds system_instruction as a plain string; the REST API requires a Content.
    The genai SDK converts it, so hand-rolling the request means inheriting the
    conversion. Without this every agent turn 400s with
    "Invalid value at 'system_instruction'" - which is what happened in production."""
    body = gl._build_body(_request())
    si = body["systemInstruction"]
    assert isinstance(si, dict), f"must be a Content object, got {type(si).__name__}"
    assert si["parts"][0]["text"] == "You are FinChat."


def test_request_without_tools_omits_the_key():
    assert "tools" not in gl._build_body(_request(with_tools=False))


# --- Round trip ---------------------------------------------------------------

class _Resp(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(monkeypatch, payload=None, exc=None, capture=None):
    monkeypatch.setattr(gl, "GATEWAY_URL", "https://gw.example")
    monkeypatch.setattr(gl, "_id_token", lambda a: None)

    def fake(req, timeout=None):
        if capture is not None:
            capture.append(json.loads(req.data))
        if exc:
            raise exc
        return _Resp(json.dumps(payload).encode())

    monkeypatch.setattr(gl.urllib.request, "urlopen", fake)


def _drain(llm, req):
    """Drive the async generator synchronously.

    Deliberately no pytest-asyncio: CI installs plain pytest, and an adapter test that
    only runs when an extra plugin happens to be present is a test that quietly stops
    running."""
    async def go():
        return [r async for r in llm.generate_content_async(req)]
    return asyncio.run(go())


def _llm():
    return gl.GatewayLlm(model="gemini-2.5-flash", agent_id="banking_assistant",
                         owner="transactions-product@datadinosaur.com")


def test_model_function_call_parses_back_into_an_llm_response(monkeypatch):
    """The return leg: a functionCall the model emitted must reach ADK intact, or the
    agent will never invoke its tool."""
    _stub(monkeypatch, payload={"outcome": "ok", "response": {
        "candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "get_account_balance",
                              "args": {"account_id": "acct-001"}}}]}}],
        "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 12},
        "modelVersion": "gemini-2.5-flash-001"}})
    out = _drain(_llm(), _request())
    assert len(out) == 1
    fc = out[0].content.parts[0].function_call
    assert fc.name == "get_account_balance" and fc.args == {"account_id": "acct-001"}
    assert out[0].usage_metadata.prompt_token_count == 40
    assert gl.counters()["transited"] == 1


def test_attribution_is_sent_with_every_turn(monkeypatch):
    captured = []
    _stub(monkeypatch, capture=captured, payload={"outcome": "ok", "response": {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}})
    _drain(_llm(), _request())
    sent = captured[0]
    assert sent["agent_id"] == "banking_assistant"
    assert sent["workload_class"] == "tool_calling_agent"
    assert sent["owner"] == "transactions-product@datadinosaur.com"


@pytest.mark.parametrize("outcome", ["pii_blocked", "budget_exceeded",
                                     "unregistered_workload"])
def test_policy_refusal_raises_and_never_falls_back(monkeypatch, outcome):
    _stub(monkeypatch, payload={"outcome": outcome, "error": "no"})
    with pytest.raises(gl.GatewayRefused) as e:
        _drain(_llm(), _request())
    assert e.value.outcome == outcome
    assert gl.counters()["blocked"] == 1
    assert gl.counters()["bypass_error"] == 0


def test_unreachable_gateway_falls_back_to_the_direct_path(monkeypatch):
    _stub(monkeypatch, exc=OSError("connection refused"))
    called = {}

    async def fake_direct(self, llm_request, stream):
        called["yes"] = True
        from google.adk.models import LlmResponse
        yield LlmResponse()

    monkeypatch.setattr(gl.GatewayLlm, "_direct", fake_direct)
    _drain(_llm(), _request())
    assert called.get("yes") is True
    assert gl.counters()["bypass_error"] == 1


# --- Selection ----------------------------------------------------------------

def test_gateway_model_returns_a_plain_string_when_unconfigured(monkeypatch):
    monkeypatch.setattr(gl, "GATEWAY_URL", "")
    assert gl.gateway_model("banking_assistant", "gemini-2.5-flash") == "gemini-2.5-flash"


def test_gateway_model_returns_an_adapter_when_configured(monkeypatch):
    monkeypatch.setattr(gl, "GATEWAY_URL", "https://gw.example")
    m = gl.gateway_model("credit_agent", "gemini-2.5-flash", owner="o@x.com")
    assert isinstance(m, gl.GatewayLlm)
    assert m.agent_id == "credit_agent" and m.owner == "o@x.com"


def test_every_wired_agent_id_exists_in_the_registry():
    """An adapter attributing to an unregistered id would produce audit rows nobody owns."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import agents_catalog
    registered = {a["id"] for a in agents_catalog.agents("dev")}
    wired = {"banking_assistant", "loan_planner", "credit_agent",
             "transaction_review_agent", "approval_agent", "notification_agent"}
    assert wired <= registered, f"unregistered: {wired - registered}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_the_two_copies_of_this_module_are_identical():
    """products/transactions/agent and products/loans/agents are separate Docker build
    contexts, so neither can import a shared module — the adapter is duplicated by
    necessity. This is the guard that keeps the duplication honest: fix one copy and
    forget the other, and the loan agents silently keep the old behaviour."""
    repo = Path(__file__).resolve().parents[3]
    a = (repo / "products/transactions/agent/gateway_llm.py").read_bytes()
    b = (repo / "products/loans/agents/gateway_llm.py").read_bytes()
    assert a == b, ("gateway_llm.py copies have diverged — copy the transactions version "
                    "over products/loans/agents/gateway_llm.py")
