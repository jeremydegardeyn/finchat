"""Guard: the API layers stay layered (ADR-0030).

A layering that lives only in a diagram is a naming convention. These are the two rules
that actually constrain the code, chosen because each has a cheap, specific failure that
a reviewer would otherwise have to notice by eye:

  LAYER-1  An experience API may not call a system API.
  LAYER-2  A process API may not touch a data store.

LAYER-1 is what stops a channel skipping the middle tier "just for this one field",
which is how the process layer becomes optional and then becomes wrong. LAYER-2 is what
stops the middle tier turning into a second copy of the domain model, which is how you
end up with two definitions of a balance.

Neither rule bans passthrough, and that matters. An experience API forwarding a single
resource unchanged is fine — the BFF does it today and is right to. The rule is about
*reaching around* the process layer for composition, which is why it matches on the
system APIs' own configuration rather than on request counts.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# `mcp_server/` and `ui/server.py` are experience APIs too (ADR-0028) and are deliberately
# NOT listed here. Both do permitted passthrough — one resource, forwarded unchanged — so
# they legitimately name the system APIs, and a marker-based rule cannot tell that apart
# from composing. Their composed views are covered where the distinction is visible: each
# has a test asserting the composed path goes through the process layer and that
# `next_action` is not recomputed locally. Listing them here would produce a failure that
# is wrong, which is how a guard gets an exception list and then gets deleted.
EXPERIENCE_DIRS = [REPO / "products" / "experience"]
PROCESS_DIRS = [REPO / "products" / "process"]

# How a caller names a system API. Matching the env vars rather than hostnames keeps the
# rule true in every environment and independent of the URLs themselves.
SYSTEM_API_MARKERS = ("TXN_API_URL", "LOAN_API_URL", "AGENT_URL", "STEWARD_URL")
# LAYER-2 reads IMPORTS, not string constants. `analyst_routing.py`'s keyword table
# legitimately contains "bigtable", "firestore" and "cloud run" — they are words an
# analyst types, not clients it uses — and a rule that cannot tell a dependency from a
# noun would be satisfied by making the router worse at its job.
DATA_STORE_IMPORTS = ("bigquery", "bigtable", "firestore", "spanner", "sqlalchemy", "psycopg")


def _sources(dirs: list[Path]) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if d.is_dir():
            out += [p for p in d.rglob("*.py") if not p.name.startswith("test_")]
    return out


def _imports(path: Path) -> set[str]:
    """Every module name this file imports, flattened to its parts."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names |= set(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            names |= set((node.module or "").split("."))
            names |= {a.name for a in node.names}
    return names


def _code_text(path: Path) -> str:
    """Everything the file actually *does*, with comments and docstrings removed.

    Matching raw source is too crude: the first version of this guard failed on
    `backends.py` because its docstring says importing bigquery would break the layering.
    A rule that fires on prose explaining the rule is a rule people work around by
    deleting the explanation, so it reads identifiers, imports and live string literals
    instead.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, ast.Import):
            parts += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            parts.append(node.module or "")
            parts += [a.name for a in node.names]
    return "\n".join(parts)


def test_there_is_something_to_check():
    """A guard over an empty directory passes for the wrong reason."""
    assert _sources(EXPERIENCE_DIRS), "no experience-layer sources found"
    assert _sources(PROCESS_DIRS), "no process-layer sources found"


@pytest.mark.parametrize("marker", SYSTEM_API_MARKERS)
def test_layer1_experience_apis_do_not_reach_system_apis(marker):
    offenders = [str(p.relative_to(REPO)) for p in _sources(EXPERIENCE_DIRS)
                 if marker in _code_text(p)]
    assert not offenders, (
        f"LAYER-1: {offenders} reference {marker}. An experience API composes through "
        "the process layer; reaching a system API directly is how the middle tier "
        "becomes optional and then becomes wrong (ADR-0030).")


@pytest.mark.parametrize("marker", DATA_STORE_IMPORTS)
def test_layer2_process_apis_do_not_touch_data_stores(marker):
    offenders = [str(p.relative_to(REPO)) for p in _sources(PROCESS_DIRS)
                 if marker in _imports(p)]
    assert not offenders, (
        f"LAYER-2: {offenders} reference {marker}. A process API reaches data only "
        "through the system APIs that own it; querying directly makes it a second copy "
        "of the domain model (ADR-0030).")


def test_the_experience_layer_knows_exactly_one_backend():
    """Stated positively, so the rule is not only a list of things to avoid."""
    for path in _sources(EXPERIENCE_DIRS):
        src = _code_text(path)
        if "urlopen" in src or "httpx" in src:
            assert "PROCESS_API_URL" in src, (
                f"LAYER-1: {path.name} makes outbound calls but never names "
                "PROCESS_API_URL — what is it calling?")


# --- how services authenticate to each other ---------------------------------
# Not a layering rule, but it lives here because it is the same kind of failure: a
# structural mistake that every local test passes over.
SERVICE_DIRS = EXPERIENCE_DIRS + PROCESS_DIRS


def test_an_id_token_helper_never_fails_silently():
    """A helper that mints an id-token must say why it could not.

    This guard used to assert something else — that every such helper call the metadata
    identity endpoint, because `fetch_id_token` "does not work on Cloud Run". That is
    false. `google.oauth2.id_token.fetch_id_token` pings the metadata server and returns
    exactly those credentials; its own source says it covers Cloud Run. Switching to the
    explicit call fixed nothing, and a guard whose stated reason is wrong is worse than
    no guard, because the next person obeys it for a reason that will mislead them.

    What actually shipped was a missing `requests` package, which `google-auth` imports
    lazily. `fetch_id_token` catches that ImportError and re-raises it as "Neither
    metadata server or valid service account credentials are found", and the helper
    turned *that* into a bare `None`. The request then went out unauthenticated and the
    caller reported the other service as unavailable. Three layers, each one discarding
    the sentence that named the fault.

    So the rule worth enforcing is the one that would have made it a one-line diagnosis:
    if a token helper can return without a token, it has to report the reason. The
    missing dependency itself is guarded separately, by
    `scripts/test_service_requirements.py`.
    """
    offenders = []
    for path in _sources(SERVICE_DIRS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.dump(fn)
            if "fetch_id_token" not in body and "IDTokenCredentials" not in body:
                continue
            handlers = [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)]
            if not handlers:
                continue  # nothing swallowed: an exception reaches the caller intact
            reports = any(
                isinstance(n, ast.Raise)
                or (isinstance(n, ast.Call) and _callee(n) in _REPORTERS)
                for n in ast.walk(fn))
            if not reports:
                offenders.append(f"{path.relative_to(REPO)}::{fn.name}")
    assert not offenders, (
        f"{offenders} can fail to mint an id-token without saying why. A bare None is "
        "indistinguishable from the callee being down, which is how a missing "
        "dependency read as a broken service for a whole deploy cycle.")


_REPORTERS = {"print", "warning", "error", "exception", "critical", "info", "log"}


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""
