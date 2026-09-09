"""Who is calling this tool, if anyone (ADR-0020).

The middleware validates a bearer token and logs the identity, then throws it away. A
tool that needs to know *who asked* — to enforce a staff gate, or to attribute an audit
row — had no way to find out. This is that plumbing, and the whole design turns on one
question: where does the identity come from?

**Not from ambient state.** The obvious implementation is a `ContextVar` set by the ASGI
middleware, and it is a trap here. A streamable-HTTP session is long-lived: the task that
handles a tool call is not necessarily the task that handled the HTTP request which
created the session. Get that wrong and a tool call is attributed to whoever opened the
session rather than whoever made the call — an identity *bleed*, which is worse than
having no identity at all, because the audit looks right.

So the identity is read from the SDK's own per-request context: `Context.request_context`
carries the Starlette `Request` for the call being handled, and the SDK sets it. The
token is re-verified rather than trusted from a header, and the result is memoised on the
`jti` so a multi-tool answer verifies once.

Over **stdio** there is no HTTP request and no token; `current()` returns None. That is
correct rather than a gap — a local client is authenticated by Cloud Run IAM as the
person running it, and scoping there comes from `FINCHAT_MCP_PERSONA`. A caller that
needs an identity must say what to do when there is none, which `require_staff` does.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Verified-claims cache, keyed by the token's jti. A tool-calling answer makes several
# calls carrying the same token; verifying each one re-checks a signature and possibly
# refetches a JWKS for no new information.
_seen: dict[str, "Caller"] = {}


def _emails(name: str) -> set[str]:
    return {e.strip().lower() for e in os.getenv(name, "").replace(";", ",").split(",")
            if e.strip()}


# The same lists the web BFF resolves personas from, so the two channels cannot drift
# about who is staff. Empty means nobody, which is the right default for a gate.
APPROVER_EMAILS = _emails("APPROVER_EMAILS")
ANALYST_EMAILS = _emails("ANALYST_EMAILS")
ADMIN_EMAILS = _emails("ADMIN_EMAILS")


@dataclass(frozen=True)
class Caller:
    """An authenticated caller. `kind` is 'user' or 'service' — a real distinction.

    A person carries an email that maps to a persona. A service carries a service
    account, and no persona: "a service asked on nobody's behalf" is a different fact
    from "a person asked", and a gate that treats them alike either locks out the
    machine callers or lets a machine inherit a human's entitlement.
    """

    email: str
    kind: str
    client_id: str = ""
    jti: str = ""

    @property
    def persona(self) -> str | None:
        if self.kind != "user":
            return None
        if self.email in ADMIN_EMAILS:
            return "admin"
        if self.email in ANALYST_EMAILS:
            return "analyst"
        if self.email in APPROVER_EMAILS:
            return "employee"
        return None  # authenticated, but not staff — a customer

    @property
    def is_staff(self) -> bool:
        return self.persona in ("analyst", "employee", "admin")


def current() -> Caller | None:
    """The caller of the tool being handled, or None over stdio / unauthenticated.

    Reads the SDK's per-request context rather than anything this module set, so the
    request it describes is the request being served.
    """
    try:
        import auth
        from mcp.server.fastmcp.server import Context  # noqa: F401
    except Exception:
        return None
    if not auth.enabled():
        return None

    request = _request()
    if request is None:
        return None
    header = request.headers.get("authorization", "")
    if header[:7].lower() != "bearer ":
        return None
    token = header[7:].strip()

    try:
        import jwt

        jti = jwt.decode(token, options={"verify_signature": False}).get("jti") or ""
    except Exception:
        jti = ""
    if jti and jti in _seen:
        return _seen[jti]

    try:
        claims = auth.verify(token)
    except Exception:
        # The middleware already refused anything invalid, so reaching here means the
        # token stopped verifying mid-session (expiry, key rotation). No identity is the
        # safe answer; it is never an escalation.
        return None

    caller = Caller(email=(claims.get("email") or "").lower(),
                    kind=claims.get("kind", "user"),
                    client_id=claims.get("client_id", ""),
                    jti=claims.get("jti", ""))
    if caller.jti:
        _seen[caller.jti] = caller
        if len(_seen) > 512:            # a session is short; this is a cache, not a store
            _seen.pop(next(iter(_seen)))
    return caller


def _request():
    """The Starlette request for the call in flight, or None."""
    try:
        import server as _srv  # the FastMCP instance lives on this module

        return _srv.mcp.get_context().request_context.request
    except Exception:
        return None


def registered_tools(email: str) -> set[str]:
    """The tool allow-list this service account is registered for (ADR-0023), or empty.

    A service never gets a persona — see `Caller.persona`, and the reason is that CLS
    under ADR-0019 evaluates against a *person*, so handing a machine a human role would
    invent a human in the audit trail. But "no persona" is not the same as "no
    entitlement". The agent registry is where a non-human principal is granted scope:
    a named accountable owner, a tool allow-list CI checks against the code, and a
    recertification date the build enforces. That is governance rather than impersonation,
    and it is the honest answer to "may a machine reach this surface".

    Empty for anything not registered, which keeps this **purely additive**: every caller
    that works today keeps working, and a registered one may be granted more.
    """
    if not email:
        return set()
    local = email.split("@")[0]
    # The env is read off the account id (`finchat-<env>-<sa_key>`) rather than from a
    # variable. A variable would have to be set correctly on every surface that imports
    # this, and being wrong would silently grant a dev identity a prod allow-list.
    parts = local.split("-")
    if len(parts) < 3 or parts[0] != "finchat":
        return set()
    env = parts[1]

    try:
        from pathlib import Path

        import loader

        catalog = loader.load(
            "agents_catalog",
            Path(__file__).resolve().parent.parent / "scripts" / "agents_catalog.py")
    except Exception:
        # The registry is not in the image, or failed to load. Granting nothing is the
        # safe direction: every caller falls back to exactly the scope it had before.
        return set()

    for agent in catalog.agents(env):
        if agent.get("status") != "active":
            continue
        # Compare the derived account id, so truncation to IAM's 30-char limit is applied
        # the same way on both sides.
        if catalog.service_account_id(agent, env) == local:
            return set(agent.get("tools") or ())
    return set()


class NotPermitted(Exception):
    """A tool refused a caller. The message is shown to the model, so it says what to do."""


def require_staff(tool: str) -> Caller | None:
    """Gate a staff-only surface. Returns the caller, or raises NotPermitted.

    Mirrors `_ASK_PERSONAS` in the web BFF: free-form analytics is a staff surface, and
    an anonymous or customer caller reaches the grounded tools instead. This answers only
    *may you call this* — what data comes back is still decided at the data layer against
    whatever credentials the call ultimately carries.

    Over stdio (`current()` is None) the caller is a developer already holding
    `run.invoker`, and `FINCHAT_MCP_PERSONA` governs scope — so the gate defers rather
    than refusing, which keeps `claude mcp add` usable without weakening the deployed
    surface, where a token is always present.
    """
    import auth

    if not auth.enabled():
        return None

    who = current()
    if who is None:
        raise NotPermitted(
            f"{tool} needs an authenticated caller. Connect through the OAuth flow so "
            "the request carries an identity.")
    if who.is_staff:
        return who
    # A registered service (ADR-0023) is permitted the tools its registry entry names.
    # This is the only route by which a machine reaches a gated tool, and it is not a
    # persona: the entry carries an accountable human owner and a recertification date,
    # and the audit still records the service account rather than a person.
    if who.kind == "service" and tool in registered_tools(who.email):
        return who
    raise NotPermitted(
        f"{tool} is a staff surface and this identity is not provisioned for it. "
        "The grounded account and knowledge-base tools are available instead.")
