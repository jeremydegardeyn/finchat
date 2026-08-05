"""ADK `BaseLlm` that routes agent turns through the Enterprise AI Gateway (ADR-0024).

Why an adapter and not an HTTP client
-------------------------------------
ADK invokes the model *inside* the framework. An agent's turn is not a prompt string — it
is a structured `generateContent` request carrying function declarations, and often a
`functionCall` the model emitted plus the `functionResponse` the runtime sent back.
Nothing about that survives being flattened into text, so an agent cannot use the
gateway's `/v1/complete` surface. It needs `/v1/generate`, which governs the request and
forwards it structurally.

Substituting this class for the model string is the whole integration: agent code,
instructions and tools are untouched.

    root_agent = Agent(name="...", model=gateway_model("banking_assistant", ...), tools=[...])

Degradation
-----------
With no `AI_GATEWAY_URL`, `gateway_model()` returns the plain model string and ADK's own
Gemini path runs — the agent still works, ungoverned, and the bypass is counted. A
governance layer that takes the product down when it hiccups gets removed within a
quarter. A policy refusal is different: it raises, because retrying a PII block or an
exhausted budget against Vertex directly would route around the control that just fired.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import AsyncGenerator

GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "").rstrip("/")
GATEWAY_TIMEOUT = float(os.getenv("AI_GATEWAY_TIMEOUT", "120"))

_lock = threading.Lock()
_counters: dict[str, int] = {"transited": 0, "bypass_error": 0, "blocked": 0}


def counters() -> dict:
    with _lock:
        c = dict(_counters)
    total = c["transited"] + c["bypass_error"]
    c["total"] = total
    c["transit_share"] = round(c["transited"] / total, 3) if total else None
    c["configured"] = bool(GATEWAY_URL)
    return c


def _count(key: str) -> None:
    with _lock:
        _counters[key] = _counters.get(key, 0) + 1


class GatewayRefused(RuntimeError):
    """The gateway applied a policy — PII, budget, or unregistered workload.

    Distinct from a transport failure on purpose. This must propagate to the agent turn
    rather than falling back, so the control cannot be routed around by retrying.
    """

    def __init__(self, outcome: str, detail: dict):
        super().__init__(f"gateway refused agent turn: {outcome}")
        self.outcome = outcome
        self.detail = detail


def _id_token(audience: str) -> str | None:
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token as gid
        return gid.fetch_id_token(google.auth.transport.requests.Request(), audience)
    except Exception:
        return None


def _build_body(llm_request) -> dict:
    """Serialize an ADK LlmRequest into a Vertex generateContent body.

    `by_alias=True` is what produces camelCase (`functionDeclarations`, `systemInstruction`)
    — google-genai types are snake_case in Python with camelCase wire aliases, and sending
    the snake_case form yields a confusing 400 rather than an obvious one.
    """
    body: dict = {
        "contents": [c.model_dump(exclude_none=True, by_alias=True)
                     for c in (llm_request.contents or [])],
    }
    cfg = getattr(llm_request, "config", None)
    if cfg is not None:
        dumped = cfg.model_dump(exclude_none=True, by_alias=True)
        # systemInstruction and tools are top-level on the wire, not inside
        # generationConfig; everything else that remains is generation config.
        for key in ("systemInstruction", "tools", "toolConfig", "safetySettings",
                    "cachedContent", "labels"):
            if key in dumped:
                body[key] = dumped.pop(key)
        # Fields ADK sets that are not valid generateContent inputs.
        for key in ("responseModalities", "speechConfig", "httpOptions"):
            dumped.pop(key, None)
        if dumped:
            body["generationConfig"] = dumped
    return body


def _post(payload: dict) -> dict | None:
    headers = {"Content-Type": "application/json"}
    tok = _id_token(GATEWAY_URL)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(f"{GATEWAY_URL}/v1/generate",
                                 data=json.dumps(payload).encode(),
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=GATEWAY_TIMEOUT) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


try:
    from google.adk.models import BaseLlm, LlmResponse
    from google.genai import types

    class GatewayLlm(BaseLlm):
        """ADK model implementation whose backend is the governed gateway."""

        agent_id: str = "unregistered"
        workload_class: str = "tool_calling_agent"
        owner: str | None = None

        @classmethod
        def supported_models(cls) -> list[str]:
            # Not registered against a model-name pattern: this class is chosen
            # explicitly per agent so the agent_id attribution is never guessed.
            return []

        async def generate_content_async(
            self, llm_request, stream: bool = False
        ) -> AsyncGenerator["LlmResponse", None]:
            self._maybe_append_user_content(llm_request)
            body = _build_body(llm_request)
            payload = _post({
                "agent_id": self.agent_id,
                "workload_class": self.workload_class,
                "owner": self.owner,
                "body": body,
                "session_id": getattr(llm_request, "session_id", None),
            })

            if payload is None:
                _count("bypass_error")
                async for r in self._direct(llm_request, stream):
                    yield r
                return

            outcome = payload.get("outcome")
            if outcome in ("pii_blocked", "budget_exceeded", "unregistered_workload"):
                _count("blocked")
                raise GatewayRefused(outcome, payload)
            if outcome != "ok":
                _count("bypass_error")
                async for r in self._direct(llm_request, stream):
                    yield r
                return

            _count("transited")
            resp = types.GenerateContentResponse.model_validate(payload["response"])
            yield LlmResponse.create(resp)

        async def _direct(self, llm_request, stream: bool):
            """Counted fallback: ADK's own Gemini path, ungoverned.

            Constructed lazily so an environment without the Gemini backend still
            imports this module.
            """
            from google.adk.models.google_llm import Gemini
            async for r in Gemini(model=self.model).generate_content_async(
                    llm_request, stream):
                yield r

    _ADK_AVAILABLE = True

except Exception:  # pragma: no cover — ADK absent (offline dev)
    GatewayLlm = None  # type: ignore[assignment]
    _ADK_AVAILABLE = False


def gateway_model(agent_id: str, model: str, *, owner: str | None = None,
                  workload_class: str = "tool_calling_agent"):
    """Return what belongs in an ADK `Agent(model=...)`.

    A `GatewayLlm` when the gateway is configured and ADK is present; otherwise the plain
    model string, so the agent keeps working on the direct path. `agent_id` must match a
    key in `scripts/agents_catalog.py` — that is what ties a model call to an owner, a
    risk tier and a recertification date.
    """
    if not (GATEWAY_URL and _ADK_AVAILABLE):
        return model
    return GatewayLlm(model=model, agent_id=agent_id, owner=owner,
                      workload_class=workload_class)
