"""LLM wrapper — Gemini via **Vertex AI**, consistent with the rest of FinChat.

FinChat does not use a Gemini API key; agents and live-eval call Gemini through Vertex
using the service account (GOOGLE_GENAI_USE_VERTEXAI=TRUE + the SA's aiplatform.user
role). This wrapper does the same. A GEMINI_API_KEY path is kept only as a local-dev
convenience. With neither configured, callers fall back to deterministic offline logic.
"""
from __future__ import annotations

import os

# Model pin (ADR-0022): FINCHAT_PIN_STEWARD if configured, else the floating alias.
MODEL = os.getenv("FINCHAT_PIN_STEWARD") or os.getenv("AGENT_MODEL", "gemini-2.5-flash")


def _vertex_enabled() -> bool:
    return (os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in ("1", "TRUE", "YES")
            and bool(os.getenv("GOOGLE_CLOUD_PROJECT")))


def llm_available() -> bool:
    if not (_vertex_enabled() or os.getenv("GEMINI_API_KEY")):
        return False
    try:
        import google.genai  # noqa: F401
        return True
    except ImportError:
        return False


def _via_gateway(prompt: str) -> str | None:
    """Governed path (ADR-0024). None when unconfigured/unreachable so the direct call
    below still runs — a governance layer that takes the steward down gets removed."""
    url = os.getenv("AI_GATEWAY_URL", "").rstrip("/")
    if not url:
        return None
    import json as _json
    import urllib.request
    body = _json.dumps({"agent_id": "steward_generator", "workload_class": "reasoning",
                        "owner": "data-steward@datadinosaur.com", "prompt": prompt}).encode()
    headers = {"Content-Type": "application/json"}
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token as _gid
        headers["Authorization"] = "Bearer " + _gid.fetch_id_token(
            google.auth.transport.requests.Request(), url)
    except Exception:
        pass  # local/unauthenticated gateway
    try:
        req = urllib.request.Request(f"{url}/v1/complete", data=body, method="POST",
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = _json.loads(r.read())
    except Exception:
        return None
    if payload.get("outcome") == "ok":
        return (payload.get("text") or "").strip()
    # A policy refusal must not silently fall through to a direct call — that would
    # route around the control that just fired.
    if payload.get("outcome") in ("pii_blocked", "budget_exceeded", "unregistered_workload"):
        raise RuntimeError(f"gateway refused: {payload.get('outcome')}")
    return None


def complete(prompt: str) -> str:
    """Return model text, or raise so callers fall back to offline logic."""
    via = _via_gateway(prompt)
    if via is not None:
        return via

    from google import genai

    if _vertex_enabled():
        client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    else:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    resp = client.models.generate_content(model=MODEL, contents=prompt)
    return (resp.text or "").strip()
