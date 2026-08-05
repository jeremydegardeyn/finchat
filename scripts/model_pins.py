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

Setting a pin — and when you cannot
-----------------------------------
Snapshot ids must be confirmed against the live API for the region in use; they are not
guessable and they change as versions retire. Run `scripts/check_pinnable.py` to probe.

**As of 2026-08-05, gemini-2.5-flash publishes no snapshot to pin to.** Verified against
us-central1: the publisher model reports `versionId: default`, only the bare alias and
`@default` return 200, and `gemini-2.5-flash-001` / `@001` / dated preview forms all 404.
The 1.5 and 2.0 generations did ship dated snapshots; 2.5 currently does not.

Two consequences, both deliberate:

* Setting FINCHAT_PIN_AGENT to an invented dated id produces config that 404s at
  runtime. An unpinnable model is reported as INFO, not a warning — a warning nobody
  can clear is one people learn to ignore, which is worse than not having it.
* **The scheduled canary is therefore the PRIMARY drift control here, not a backup.**
  If the provider repoints the alias, nothing in the request or the response reveals it;
  only the fixed golden set moving does.

    export FINCHAT_PIN_AGENT=<snapshot-id>   # once one exists
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


# Whether the provider publishes a snapshot to pin to, VERIFIED against the live API
# rather than assumed. This is a fact about someone else's product and it will change
# without telling us — re-check with scripts/check_pinnable.py and update the date.
PINNABLE_VERIFIED = "2026-08-05"
PINNABLE = {
    "gemini-2.5-flash": False,  # versionId=default; -001 / @001 / dated forms all 404
    "gemini-2.5-pro": False,    # same family, same publishing pattern
    "managed": False,           # Conversational Analytics: version governed by the service
}


def is_pinnable(model: str) -> bool:
    """True when a snapshot exists to pin to.

    Unknown models default to True on purpose: a NEW model is pinnable-until-proven-
    otherwise, so it surfaces as a warning to action rather than being silently excused
    by an omission in the table above.
    """
    return PINNABLE.get(model, True)


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
            "pinnable": is_pinnable(alias),
            "workload_class": WORKLOAD_CLASS[site],
        }
        for site, alias in ALIASES.items()
    ]


if __name__ == "__main__":
    import json
    print(json.dumps(pin_report(), indent=2))
