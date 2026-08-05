#!/usr/bin/env python3
"""
Model version pins — single source of truth (ADR-0022).

Why pinning matters, concretely
-------------------------------
Published research measured GPT-4's prime-vs-composite identification accuracy falling
from 84% to 51% between March and June 2023 on an *unchanged prompt*. A provider-side
change nobody requested, nobody was notified of, and nobody could roll back cut task
accuracy by a third. That is the entire case for pinning, and it maps to FINOS
AIR-PREV-010 (AI Model Version Pinning), which treats model versioning as configuration
management plus supply-chain risk.

The two halves of the control
-----------------------------
1. **Request a pinned snapshot** rather than a floating alias. That is `PINS` below.
2. **Log what actually served.** Vertex returns a `modelVersion` field on every
   `generateContent` response. Requesting a version and recording the version that
   answered are different facts, and only the second one is evidence. `served_version()`
   is the helper every call site uses.

Half two works today and is the more important half — an alias whose serving version is
logged per turn is a materially better position than a pin nobody verifies.

Setting a pin
-------------
Snapshot ids must be confirmed against the Model Garden listing for the region in use;
they are not guessable and they change as versions are retired. Until a pin is set, a
call site runs its alias and `verify_agent_registry.py` reports PIN-1 for the agents
behind it — a declared alias, not an accidental one.

    export FINCHAT_PIN_AGENT=<snapshot-id-from-model-garden>
"""
from __future__ import annotations

import os

# Floating aliases, by logical call site. These are what FinChat requests when no pin is
# configured. Keys match the FINCHAT_PIN_<KEY> environment variable that overrides them.
ALIASES = {
    "AGENT": "gemini-2.5-flash",      # Banking Assistant + loan agents (M2, M3)
    "ROUTER": "gemini-2.5-flash",     # analyst intent router (BFF)
    "SEMANTICS": "gemini-2.5-flash",  # data-model semantics answerer (BFF)
    "JUDGE": "gemini-2.5-flash",      # LLM-as-judge, live eval + steward gate (M6)
    "STEWARD": "gemini-2.5-flash",    # steward planner/generator (M6)
}

# Workload class per call site — used by the unit-economics rollup (docs/22) so cost per
# successful task is reported by class rather than as one undifferentiated number.
WORKLOAD_CLASS = {
    "AGENT": "tool_calling_agent",
    "ROUTER": "classification",
    "SEMANTICS": "grounded_generation",
    "JUDGE": "evaluation",
    "STEWARD": "reasoning",
}


def model_for(site: str) -> str:
    """Model id to request for a call site: the pin if configured, else the alias.

    `site` is one of the keys in ALIASES (case-insensitive).
    """
    key = site.upper()
    if key not in ALIASES:
        raise KeyError(f"unknown call site '{site}'; known: {sorted(ALIASES)}")
    return os.getenv(f"FINCHAT_PIN_{key}") or ALIASES[key]


def is_pinned(site: str) -> bool:
    """True when this call site runs a configured snapshot rather than a floating alias."""
    return bool(os.getenv(f"FINCHAT_PIN_{site.upper()}"))


def served_version(response_json: dict) -> str | None:
    """Extract the version that actually served from a Vertex generateContent response.

    This is the evidence half of the control. Vertex returns `modelVersion` at the top
    level of the response; when it is absent (older surfaces, managed services that do
    not expose it) the caller should log None rather than assume the requested version
    served — recording an assumption as a fact is how pinning becomes theatre.
    """
    if not isinstance(response_json, dict):
        return None
    v = response_json.get("modelVersion")
    return v if isinstance(v, str) and v else None


def pin_report() -> list[dict]:
    """Per-call-site pinning posture, for the registry gate and the Admin UI."""
    return [
        {
            "site": site,
            "requested": model_for(site),
            "alias": alias,
            "pinned": is_pinned(site),
            "workload_class": WORKLOAD_CLASS[site],
        }
        for site, alias in ALIASES.items()
    ]


if __name__ == "__main__":
    import json
    print(json.dumps(pin_report(), indent=2))
