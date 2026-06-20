"""LLM wrapper with an OFFLINE fallback (mirrors the rest of FinChat).

The harness, demo, and tests run deterministically with no key. If GEMINI_API_KEY
is set and google-genai is installed, real Gemini calls are made instead. This is
the single swap point for the model provider.
"""
from __future__ import annotations

import os

MODEL = os.getenv("AGENT_MODEL", "gemini-2.5-flash")


def llm_available() -> bool:
    if not os.getenv("GEMINI_API_KEY"):
        return False
    try:
        import google.genai  # noqa: F401
        return True
    except ImportError:
        return False


def complete(prompt: str) -> str:
    """Return model text, or raise so callers fall back to offline logic."""
    from google import genai  # lazy import; only when available

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(model=MODEL, contents=prompt)
    return (resp.text or "").strip()
