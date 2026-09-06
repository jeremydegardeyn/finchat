"""Guard: a service that uses google-auth's requests transport must depend on requests."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVICE_DIRS = [REPO / "products" / "experience", REPO / "products" / "process"]


def test_google_auth_requests_transport_has_its_dependency():
    """`google.auth.transport.requests` imports the `requests` package lazily.

    Installing google-auth alone is enough to import the module and NOT enough to use
    it: the first token mint raises ImportError, the helper returns None, and the call
    goes out unauthenticated. That shipped — the process API reported its backends as
    unavailable when the real fault was a missing dependency in its own image.
    """
    offenders = []
    for d in SERVICE_DIRS:
        for req in d.rglob("requirements.txt"):
            src = "\n".join(p.read_text(encoding="utf-8")
                            for p in req.parent.glob("*.py") if not p.name.startswith("test_"))
            if "google.auth.transport.requests" not in src:
                continue
            if "requests==" not in req.read_text(encoding="utf-8"):
                offenders.append(str(req.relative_to(REPO)))
    assert not offenders, (
        f"{offenders} use google.auth.transport.requests but do not pin `requests`. "
        "google-auth alone imports fine and fails at the first token mint.")
