"""LLM wrapper — Gemini via **Vertex AI**, consistent with the rest of FinChat.

FinChat does not use a Gemini API key; agents and live-eval call Gemini through Vertex
using the service account (GOOGLE_GENAI_USE_VERTEXAI=TRUE + the SA's aiplatform.user
role). This wrapper does the same. A GEMINI_API_KEY path is kept only as a local-dev
convenience. With neither configured, callers fall back to deterministic offline logic.
"""
from __future__ import annotations

import os

MODEL = os.getenv("AGENT_MODEL", "gemini-2.5-flash")


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


def complete(prompt: str) -> str:
    """Return model text, or raise so callers fall back to offline logic."""
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
