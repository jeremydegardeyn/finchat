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


def _has_match(node) -> bool:
    """Depth-first search for a MATCH_FOUND anywhere under this node."""
    if isinstance(node, dict):
        if node.get("matchState") == "MATCH_FOUND":
            return True
        return any(_has_match(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_match(v) for v in node)
    return False


def matched_filters(sanitization_result: dict) -> list[str]:
    """Names of the detectors that fired — e.g. ["pi_and_jailbreak", "sdp"].

    This is the *only* thing we carry off the box about a violation: which detector matched,
    never what it matched on. The flagged text stays in Model Armor's own sanitize log,
    behind GCP IAM (docs/26 F3).

    `filterResults` has been documented both as a map keyed by filter name and as a list of
    per-filter objects, so this reads either. Unknown shapes yield an empty list rather than
    raising: a screening decision must not depend on parsing cosmetics.
    """
    results = (sanitization_result or {}).get("filterResults") or {}
    names: list[str] = []
    if isinstance(results, dict):
        for name, value in results.items():
            if _has_match(value):
                names.append(str(name))
    elif isinstance(results, list):
        for i, value in enumerate(results):
            if not _has_match(value):
                continue
            key = next((k for k in value if k.endswith("FilterResult")), None) if isinstance(value, dict) else None
            names.append(key[:-len("FilterResult")] if key else f"filter_{i}")
    return sorted(set(names))


async def _sanitize(method: str, payload: dict) -> tuple[bool, list[str]]:
    """Return (flagged, matched filter names). Flagged means MATCH_FOUND."""
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
    result = r.json().get("sanitizationResult", {}) or {}
    flagged = result.get("filterMatchState", "") == "MATCH_FOUND"
    return flagged, (matched_filters(result) if flagged else [])


async def _screen_detailed(method: str, payload: dict) -> dict:
    """{allowed, reason, filters}. Fails open unless ARMOR_FAIL_CLOSED."""
    if not enabled():
        return {"allowed": True, "reason": "armor-disabled", "filters": []}
    try:
        flagged, filters = await _sanitize(method, payload)
        return {
            "allowed": not flagged,
            "reason": "flagged" if flagged else "clean",
            "filters": filters,
        }
    except Exception as e:  # network / auth / API error
        return {
            "allowed": not FAIL_CLOSED,
            "reason": f"armor-error:{type(e).__name__}",
            "filters": [],
        }


async def _screen(method: str, payload: dict) -> tuple[bool, str]:
    """(allowed, reason) — the original two-value contract, kept for existing callers."""
    r = await _screen_detailed(method, payload)
    return r["allowed"], r["reason"]


async def screen_prompt(text: str) -> tuple[bool, str]:
    return await _screen("sanitizeUserPrompt", {"user_prompt_data": {"text": text}})


async def screen_response(text: str) -> tuple[bool, str]:
    return await _screen("sanitizeModelResponse", {"model_response_data": {"text": text}})


async def screen_prompt_detailed(text: str) -> dict:
    return await _screen_detailed("sanitizeUserPrompt", {"user_prompt_data": {"text": text}})


async def screen_response_detailed(text: str) -> dict:
    return await _screen_detailed("sanitizeModelResponse", {"model_response_data": {"text": text}})
