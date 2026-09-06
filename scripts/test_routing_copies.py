"""Guard: the two copies of the analyst routing module stay byte-identical (ADR-0030).

The routing decision is owned by the process layer, and the BFF needs it in-process
because the classifiers carry credentials the BFF holds — the gateway's `on_behalf_of`
and the platform token for Vertex (ADR-0019). One file would be better and is not
reachable: every service image is built with its own directory as the Docker context, so
`ui/` cannot COPY out of `products/`. Moving the UI to a repo-root context would change
how the live front end deploys, which is a worse trade than a copy that cannot drift.

So it is duplicated deliberately and pinned here, the same way `gateway_llm.py` already
is. The failure this prevents is specific and quiet: a keyword added to one copy after a
misrouted question, and the other copy still routing it wrong — which is the exact class
of bug the tables in this module were written to fix in the first place.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OWNER = REPO / "products" / "process" / "api" / "analyst_routing.py"
COPY = REPO / "ui" / "intent.py"


def test_both_copies_exist():
    assert OWNER.is_file(), f"{OWNER} is missing — the process layer owns this module"
    assert COPY.is_file(), f"{COPY} is missing — the BFF imports it as `intent`"


def test_the_two_copies_are_identical():
    owner, copy = OWNER.read_bytes(), COPY.read_bytes()
    if owner == copy:
        return
    # Name the direction, because the copy is the one that gets edited by accident.
    raise AssertionError(
        f"{COPY.relative_to(REPO)} has drifted from {OWNER.relative_to(REPO)} "
        f"({len(owner)} vs {len(copy)} bytes). The process layer owns the rule; sync with:\n"
        f"    cp products/process/api/analyst_routing.py ui/intent.py")


def test_the_copy_carries_the_names_the_bff_imports():
    """A sync that dropped an export would fail at import time in the container, not here.

    Cheap to assert, and it fails with a sentence instead of a traceback from uvicorn.
    """
    src = COPY.read_text(encoding="utf-8")
    for name in ("KB_WORDS", "AN_WORDS", "SEM_WORDS", "PLATFORM_WORDS",
                 "def hits", "def heuristic_intent", "def classify_async"):
        assert name in src, f"{name} missing from ui/intent.py — server.py imports it"
