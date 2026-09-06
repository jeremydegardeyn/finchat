"""Guard: a service that uses google-auth's requests transport must depend on requests."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def _requirements_files():
    """Every deployable unit in the repo, not a hand-kept list.

    The first version of this guard scanned `products/experience` and `products/process`
    — the two directories I had just fixed. `mcp_server/` has the same import and the
    same gap, and would have shipped it: a guard scoped to the place a bug was found
    only catches that bug.
    """
    for req in REPO.rglob("requirements.txt"):
        if not SKIP.intersection(req.relative_to(REPO).parts):
            yield req


def test_google_auth_requests_transport_has_its_dependency():
    """`google.auth.transport.requests` imports the `requests` package lazily.

    Installing google-auth alone is enough to import the module and NOT enough to use
    it: the first token mint raises ImportError, the helper returns None, and the call
    goes out unauthenticated. That shipped — the process API reported its backends as
    unavailable when the real fault was a missing dependency in its own image.

    Most of these services do get `requests` today, transitively, because a
    `google-cloud-*` client pulls `google-api-core`, which requires it. That is luck
    rather than a dependency: it disappears the moment a service drops its BigQuery
    client, which is exactly what LAYER-2 asks the process layer to do. A package you
    import directly is a package you declare.
    """
    offenders = []
    for req in _requirements_files():
        src = "\n".join(p.read_text(encoding="utf-8")
                        for p in req.parent.rglob("*.py") if not p.name.startswith("test_"))
        if "google.auth.transport.requests" not in src:
            continue
        if "requests==" not in req.read_text(encoding="utf-8"):
            offenders.append(str(req.relative_to(REPO)).replace("\\", "/"))
    assert not offenders, (
        f"{offenders} use google.auth.transport.requests but do not pin `requests`. "
        "google-auth alone imports fine and fails at the first token mint.")
