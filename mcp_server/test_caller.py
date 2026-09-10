"""Tests for caller identity in tools (ADR-0020 plumbing).

The failure this design exists to avoid is an identity *bleed*: a tool call attributed to
whoever opened the streamable-HTTP session rather than whoever made the call. That is
worse than having no identity, because the audit looks right and is wrong. So the tests
below care more about *where* the identity comes from than about the happy path.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ISSUER = "https://auth.example"
RESOURCE = "https://mcp.example/mcp"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.setenv("FINCHAT_MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("FINCHAT_MCP_RESOURCE", RESOURCE)
    monkeypatch.setenv("APPROVER_EMAILS", "officer@bank.example")
    monkeypatch.setenv("ANALYST_EMAILS", "analyst@bank.example")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@bank.example")
    auth = _load("finchat_caller_auth", "auth.py")
    caller = _load("finchat_caller", "caller.py")
    sys.modules["auth"] = auth
    caller._seen.clear()
    return caller, auth


class _Headers(dict):
    def get(self, k, default=""):
        return dict.get(self, k.lower(), default)


class _Request:
    def __init__(self, token: str | None):
        self.headers = _Headers(
            {"authorization": f"Bearer {token}"} if token else {})


def _arrange(mod, monkeypatch, token, claims):
    caller, auth = mod
    monkeypatch.setattr(caller, "_request", lambda: _Request(token))
    monkeypatch.setattr(auth, "verify", lambda t: claims if t == token else
                        (_ for _ in ()).throw(auth.Unauthorized("no")))
    return caller


def _claims(**over):
    base = {"email": "analyst@bank.example", "kind": "user",
            "client_id": "mcp-1", "jti": "j1"}
    base.update(over)
    return base


# --- where the identity comes from -------------------------------------------
def test_identity_comes_from_the_request_being_served(mod, monkeypatch):
    """The whole point. A ContextVar set by the middleware would survive across calls on
    a long-lived session and attribute one caller's tool call to another. Reading the
    SDK's per-request context means the answer changes when the request changes."""
    caller = _arrange(mod, monkeypatch, "tok-a", _claims(email="analyst@bank.example",
                                                         jti="ja"))
    assert caller.current().email == "analyst@bank.example"

    # Same process, same session, a different caller's request now in flight.
    _arrange(mod, monkeypatch, "tok-b",
             _claims(email="officer@bank.example", jti="jb"))
    assert caller.current().email == "officer@bank.example"


def test_a_cached_identity_is_keyed_to_its_own_token(mod, monkeypatch):
    """The memo must not become the bleed it was avoiding: caching by anything other
    than the token itself would return the first caller for every later one."""
    caller = _arrange(mod, monkeypatch, "tok-a", _claims(email="analyst@bank.example",
                                                         jti="ja"))
    assert caller.current().email == "analyst@bank.example"
    _arrange(mod, monkeypatch, "tok-b", _claims(email="admin@bank.example", jti="jb"))
    assert caller.current().email == "admin@bank.example"
    assert set(caller._seen) == {"ja", "jb"}


def test_no_request_means_no_caller(mod, monkeypatch):
    """stdio, or a call outside a request. None is correct — and every consumer has to
    decide what that means rather than inheriting somebody else's identity."""
    caller, _ = mod
    monkeypatch.setattr(caller, "_request", lambda: None)
    assert caller.current() is None


def test_a_known_caller_is_never_reused_when_the_request_is_gone(mod, monkeypatch):
    """The bleed, stated exactly.

    Mutation testing found this gap: the "no request" test above ran with an EMPTY cache,
    so an implementation that falls back to "whoever we saw last" passed it. Populate the
    cache first, then take the request away — anything other than None here means one
    caller's identity can be handed to another call.
    """
    caller = _arrange(mod, monkeypatch, "tok-a", _claims(email="admin@bank.example",
                                                         jti="ja"))
    assert caller.current().email == "admin@bank.example"
    assert caller._seen, "cache must be populated for this test to mean anything"

    monkeypatch.setattr(caller, "_request", lambda: None)
    assert caller.current() is None, "a remembered caller leaked into a request without one"


def test_a_token_that_stops_verifying_yields_no_identity(mod, monkeypatch):
    """Expiry or key rotation mid-session. No identity is safe; a stale one is not."""
    caller, auth = mod
    monkeypatch.setattr(caller, "_request", lambda: _Request("expired"))
    monkeypatch.setattr(auth, "verify",
                        lambda t: (_ for _ in ()).throw(auth.Unauthorized("expired")))
    assert caller.current() is None


def test_a_malformed_authorization_header_yields_no_identity(mod, monkeypatch):
    caller, _ = mod
    for header in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer"}):
        monkeypatch.setattr(caller, "_request",
                            lambda h=header: type("R", (), {"headers": _Headers(h)})())
        assert caller.current() is None


# --- personas -----------------------------------------------------------------
def test_personas_resolve_from_the_same_lists_the_web_channel_uses(mod, monkeypatch):
    caller = _arrange(mod, monkeypatch, "t", _claims())
    expected = {"admin@bank.example": "admin", "analyst@bank.example": "analyst",
                "officer@bank.example": "employee"}
    for email, persona in expected.items():
        _arrange(mod, monkeypatch, "t", _claims(email=email, jti=email))
        assert caller.current().persona == persona
        assert caller.current().is_staff


def test_an_authenticated_customer_is_not_staff(mod, monkeypatch):
    """Authenticated is not the same as entitled. A customer who signed in correctly
    still must not reach a staff surface."""
    caller = _arrange(mod, monkeypatch, "t",
                      _claims(email="customer@bank.example", jti="jc"))
    who = caller.current()
    assert who.persona is None and who.is_staff is False


def test_a_service_never_inherits_a_persona(mod, monkeypatch):
    """A service account's email could sit in a staff list by accident, or by a
    copy-paste. "A service asked on nobody's behalf" is not a person, and must not
    acquire a person's entitlement."""
    caller = _arrange(mod, monkeypatch, "t",
                      _claims(email="analyst@bank.example", kind="service", jti="js"))
    who = caller.current()
    assert who.kind == "service"
    assert who.persona is None and who.is_staff is False


# --- the gate -----------------------------------------------------------------
def test_the_staff_gate_refuses_a_customer_and_says_what_is_available(mod, monkeypatch):
    caller = _arrange(mod, monkeypatch, "t",
                      _claims(email="customer@bank.example", jti="jc"))
    with pytest.raises(caller.NotPermitted) as excinfo:
        caller.require_staff("ask_analytics")
    assert "staff surface" in str(excinfo.value)
    assert "knowledge-base tools are available" in str(excinfo.value)


def test_the_staff_gate_refuses_when_there_is_no_identity(mod, monkeypatch):
    """Deployed and enforcing, a token is always present — so no identity here means
    something is wrong, and the gate fails closed."""
    caller, _ = mod
    monkeypatch.setattr(caller, "_request", lambda: None)
    with pytest.raises(caller.NotPermitted):
        caller.require_staff("ask_analytics")


def test_the_staff_gate_admits_staff(mod, monkeypatch):
    caller = _arrange(mod, monkeypatch, "t", _claims(jti="ja"))
    assert caller.require_staff("ask_analytics").persona == "analyst"


def test_the_gate_defers_over_stdio_rather_than_breaking_local_use(monkeypatch):
    """With OAuth unconfigured this is a local stdio server: the caller is a developer
    who already holds run.invoker, and FINCHAT_MCP_PERSONA governs scope. Refusing here
    would break `claude mcp add` without protecting the deployed surface, where a token
    is always present."""
    monkeypatch.delenv("FINCHAT_MCP_OAUTH_ISSUER", raising=False)
    monkeypatch.delenv("FINCHAT_MCP_RESOURCE", raising=False)
    auth = _load("finchat_caller_auth_off", "auth.py")
    sys.modules["auth"] = auth
    caller = _load("finchat_caller_off", "caller.py")
    assert auth.enabled() is False
    assert caller.require_staff("ask_analytics") is None


def test_empty_staff_lists_admit_nobody(mod, monkeypatch):
    """The same fail-closed default as every other allow-list here."""
    caller, _ = mod
    monkeypatch.setattr(caller, "APPROVER_EMAILS", set())
    monkeypatch.setattr(caller, "ANALYST_EMAILS", set())
    monkeypatch.setattr(caller, "ADMIN_EMAILS", set())
    _arrange(mod, monkeypatch, "t", _claims(jti="jz"))
    assert caller.current().is_staff is False


# --- registered service entitlements (ADR-0023) -------------------------------
# The question these answer: a machine gets no persona, so is a staff surface simply
# closed to it forever? No — the agent registry grants scope to a non-human principal
# with a named owner and a recertification date. That is governance, not impersonation,
# and the tests below care most that it stayed ADDITIVE.

@pytest.fixture()
def registry(mod, monkeypatch):
    """Make `import loader` resolve the way it does in the image (cwd is mcp_server/)."""
    monkeypatch.syspath_prepend(str(HERE))
    caller, _ = mod
    return caller


def _service(mod, monkeypatch, email, tool_env="dev"):
    return _arrange(mod, monkeypatch,
                    "t", _claims(email=email, kind="service", jti=email))


def test_a_registered_service_is_admitted_to_a_tool_it_is_registered_for(
        registry, mod, monkeypatch):
    caller = _service(mod, monkeypatch,
                      "finchat-dev-aws-mcp@strongsville-city-schools.iam.gserviceaccount.com")
    assert caller.require_staff("get_account_balance").kind == "service"


def test_a_registered_service_is_refused_a_tool_outside_its_allow_list(
        registry, mod, monkeypatch):
    """The allow-list is the control. A registry entry is not a blanket pass — the
    approver-only tools are absent from it on purpose."""
    caller = _service(mod, monkeypatch,
                      "finchat-dev-aws-mcp@strongsville-city-schools.iam.gserviceaccount.com")
    with pytest.raises(caller.NotPermitted):
        caller.require_staff("get_loan_audit")


def test_an_unregistered_service_is_still_refused(registry, mod, monkeypatch):
    """The regression that matters. The AI gateway, the BFF and CI all call as services
    and none of them is in the registry; if registration became a REQUIREMENT rather than
    an additional grant, this change would have taken them all down."""
    caller = _service(mod, monkeypatch,
                      "ai-gateway-sa@strongsville-city-schools.iam.gserviceaccount.com")
    with pytest.raises(caller.NotPermitted):
        caller.require_staff("get_account_balance")


def test_a_registered_service_still_has_no_persona(registry, mod, monkeypatch):
    """An entitlement is not a persona. If this ever flips, the audit starts naming a
    human role for something no human did."""
    caller = _service(mod, monkeypatch,
                      "finchat-dev-aws-mcp@strongsville-city-schools.iam.gserviceaccount.com")
    who = caller.current()
    assert who.persona is None and who.is_staff is False


def test_the_environment_comes_from_the_account_id(registry, mod):
    """A dev identity must not resolve a prod allow-list. The env is read off the account
    id rather than a variable, because a variable set wrongly fails silently and in the
    permissive direction."""
    caller, _ = mod
    dev = caller.registered_tools(
        "finchat-dev-aws-mcp@strongsville-city-schools.iam.gserviceaccount.com")
    prod = caller.registered_tools(
        "finchat-prod-aws-mcp@strongsville-city-schools.iam.gserviceaccount.com")
    assert dev and prod, "both environments register the harness"
    # An unrecognised env segment still resolves, because the catalog's allow-lists do not
    # vary by environment — only the account id does. That is safe rather than sloppy: an
    # account like `finchat-nosuchenv-aws-mcp` does not exist, so it cannot authenticate,
    # and FINCHAT_MCP_SERVICE_CALLERS is what decides which accounts get that far. This
    # asserts the property rather than pretending the parser validates envs.
    assert caller.registered_tools(
        "finchat-nosuchenv-aws-mcp@x.iam.gserviceaccount.com") == dev


def test_an_unrecognisable_identity_grants_nothing(registry, mod):
    caller, _ = mod
    for email in ("", "someone@example.com", "not-finchat-shaped@x.iam.gserviceaccount.com",
                  "finchat-dev@x.iam.gserviceaccount.com"):
        assert caller.registered_tools(email) == set(), email


def test_the_registry_is_shipped_in_the_image():
    """caller.py reads the registry at runtime. A missing COPY makes every registered
    service silently lose its entitlement — a failure that looks like a permissions bug."""
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts/agents_catalog.py" in dockerfile
