"""
Model Armor screening for the agent path (ADR-0008).

Screens user prompts and model responses via the Model Armor REST API:
  * prompt injection / jailbreak
  * sensitive data (SDP/DLP)
  * malicious URLs
  * harmful content (RAI)

Gated by env (GCP_PROJECT + MODEL_ARMOR_TEMPLATE). If unset or on any error it
fails OPEN (passes through) so screening never takes the app down — flip
ARMOR_FAIL_CLOSED=1 to fail closed in a hardened deployment.
"""
from __future__ import annotations

import os

PROJECT = os.getenv("GCP_PROJECT", "")
LOCATION = os.getenv("MODEL_ARMOR_LOCATION", "us-central1")
TEMPLATE = os.getenv("MODEL_ARMOR_TEMPLATE", "")  # short template id
FAIL_CLOSED = os.getenv("ARMOR_FAIL_CLOSED", "").lower() in ("1", "true", "yes")


def enabled() -> bool:
    return bool(PROJECT and TEMPLATE)


async def _sanitize(method: str, payload: dict) -> bool:
    """Return True if Model Armor flagged a violation (MATCH_FOUND)."""
    import httpx
    from google.auth import default
    from google.auth.transport.requests import Request

    creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    url = (f"https://modelarmor.{LOCATION}.rep.googleapis.com/v1/projects/"
           f"{PROJECT}/locations/{LOCATION}/templates/{TEMPLATE}:{method}")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(url, json=payload, headers={"Authorization": f"Bearer {creds.token}"})
    r.raise_for_status()
    state = (r.json().get("sanitizationResult", {}) or {}).get("filterMatchState", "")
    return state == "MATCH_FOUND"


async def _screen(method: str, payload: dict) -> tuple[bool, str]:
    """(allowed, reason). Fails open unless ARMOR_FAIL_CLOSED."""
    if not enabled():
        return True, "armor-disabled"
    try:
        flagged = await _sanitize(method, payload)
        return (not flagged), ("flagged" if flagged else "clean")
    except Exception as e:  # network / auth / API error
        return (not FAIL_CLOSED), f"armor-error:{type(e).__name__}"


async def screen_prompt(text: str) -> tuple[bool, str]:
    return await _screen("sanitizeUserPrompt", {"user_prompt_data": {"text": text}})


async def screen_response(text: str) -> tuple[bool, str]:
    return await _screen("sanitizeModelResponse", {"model_response_data": {"text": text}})
