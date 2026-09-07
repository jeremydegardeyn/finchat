"""FinChat MCP OAuth proxy — the authorization server hosted clients need (ADR-0020).

A managed MCP client (Claude's connector flow, a partner's agent) cannot be handed a
static bearer token: there is no field for one. It does OAuth 2.1 with Dynamic Client
Registration — it self-registers, runs authorization-code + PKCE, and expects a token
endpoint. **Google does not support DCR**; clients are created by hand in the Console.
That gap is the entire reason this service exists. Remove it and you would not need it.

So this presents a spec-compliant authorization server *to the client*, and federates the
human login *to Google* as an ordinary OIDC relying party. The token the client ends up
carrying is minted here, not Google's — which is what lets the lifetime be short, the
audience be one MCP resource (RFC 8707), and the IdP's own tokens stay off the client.

**It is not in the data path.** Once a token is issued the client talks to the MCP server
directly; this service is never a hop in a tool call, so it cannot add latency to one or
take tools down when it restarts.

Everything here fails closed. `OAUTH_ALLOWED_DOMAINS` unset means nobody is allowed in,
not everybody — an authorization server is the one place where a permissive default is
the vulnerability rather than a convenience.
"""
from __future__ import annotations

import os
import secrets
import time
import urllib.parse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

import oauth

app = FastAPI(
    title="FinChat MCP OAuth proxy",
    version="1.0.0",
    description="OAuth 2.1 + DCR in front of the MCP resource server (ADR-0020).",
)

ISSUER = os.getenv("OAUTH_ISSUER", "").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"

# The org IS the boundary (ADR-0020): accept an identity when the Google ID token's `hd`
# claim matches. Empty means deny everyone, deliberately.
ALLOWED_DOMAINS = {d.strip().lower() for d in
                   os.getenv("OAUTH_ALLOWED_DOMAINS", "").split(",") if d.strip()}
# A narrower override for accounts outside the Workspace domain (a gmail.com auditor
# persona, say). Also empty by default.
ALLOWED_EMAILS = {e.strip().lower() for e in
                  os.getenv("OAUTH_ALLOWED_EMAILS", "").split(",") if e.strip()}

# RFC 8707. A token is minted for ONE resource, and only for a resource named here — so a
# client cannot ask for, and this server cannot accidentally mint, a token whose audience
# is some other service that also trusts this issuer.
ALLOWED_RESOURCES = {r.strip().rstrip("/") for r in
                     os.getenv("OAUTH_ALLOWED_RESOURCES", "").split(",") if r.strip()}

SCOPES = ["mcp:tools", "mcp:resources"]

KEY = oauth.SigningKey()
STORE = oauth.Store()

# Pending Google round-trips: our own state -> the client's authorize request. Held only
# between the redirect to Google and the callback, which is seconds.
_PENDING: dict[str, dict] = {}
_PENDING_TTL = 600


def _fail(status: int, error: str, description: str) -> HTTPException:
    """OAuth errors are a defined shape; a caller parses them. Never leak internals."""
    return HTTPException(status, {"error": error, "error_description": description})


def _issuer(request: Request) -> str:
    if ISSUER:
        return ISSUER
    # Behind Cloud Run the scheme is in X-Forwarded-Proto; building the issuer from
    # request.url would advertise http:// and every client would refuse it.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


def _redirect_ok(uri: str, registered: list[str]) -> bool:
    """Exact match against what the client registered. No prefix matching, no wildcards.

    Prefix matching is how redirect_uri validation is usually got wrong: a registered
    `https://app.example.com/cb` then also permits `https://app.example.com/cb.evil.com`
    or `.../cb/../..`, and the authorization code goes to the attacker.
    """
    return uri in registered


def _usable_redirect(uri: str) -> bool:
    parsed = urllib.parse.urlparse(uri)
    if parsed.fragment:
        return False
    if parsed.scheme == "https":
        return True
    # Loopback for native/desktop clients, per RFC 8252. Nothing else over plaintext.
    return parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")


# --- discovery ---------------------------------------------------------------
@app.get("/.well-known/oauth-authorization-server", tags=["discovery"])
def metadata(request: Request):
    """RFC 8414. The first thing a hosted client fetches."""
    issuer = _issuer(request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "scopes_supported": SCOPES,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],  # never "plain"
        "token_endpoint_auth_methods_supported": ["none"],
        "resource_indicators_supported": True,
        # Not part of RFC 8414. Here because both are deployment faults that otherwise
        # present as intermittent logins: an ephemeral key rejects tokens it just issued
        # after a cold start, and a non-durable store cannot redeem a code on a second
        # instance. Better on the metadata endpoint than in a support thread.
        "x_finchat_signing_key_ephemeral": KEY.ephemeral,
        "x_finchat_store_durable": STORE.durable,
        "x_finchat_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
                                     and (ALLOWED_DOMAINS or ALLOWED_EMAILS)
                                     and ALLOWED_RESOURCES),
    }


@app.get("/.well-known/jwks.json", tags=["discovery"])
def jwks():
    return KEY.jwks()


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok"}


# --- dynamic client registration (RFC 7591) ----------------------------------
@app.post("/register", status_code=201, tags=["oauth"])
async def register(request: Request):
    """Open registration, which is what the spec requires and what hosted clients use.

    Open is less alarming than it sounds: a client_id is an identifier, not a permission.
    Registering one grants nothing — a token still requires a human to complete Google
    sign-in AND to pass the domain check. What registration must not do is accept a
    redirect target that could receive somebody else's authorization code, so the URIs
    are validated here rather than at /authorize, where a bad one is already in flight.
    """
    try:
        body = await request.json()
    except Exception:
        raise _fail(400, "invalid_client_metadata", "body must be JSON") from None

    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        raise _fail(400, "invalid_redirect_uri", "redirect_uris must be a non-empty list")
    if len(uris) > 8:
        raise _fail(400, "invalid_redirect_uri", "too many redirect_uris")
    for uri in uris:
        if not _usable_redirect(uri):
            raise _fail(400, "invalid_redirect_uri",
                        f"{uri} must be https, or http on a loopback host, with no fragment")

    client = oauth.Client(
        client_id=f"mcp-{secrets.token_urlsafe(18)}",
        client_name=str(body.get("client_name") or "unnamed")[:120],
        redirect_uris=uris,
    )
    STORE.put_client(client)
    return JSONResponse({
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at),
        "redirect_uris": client.redirect_uris,
        "client_name": client.client_name,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        # Public client: no secret to leak from a desktop app or a browser, PKCE does
        # the binding instead.
        "token_endpoint_auth_method": "none",
    }, status_code=201)


# --- authorization -----------------------------------------------------------
@app.get("/authorize", tags=["oauth"])
def authorize(request: Request, client_id: str = "", redirect_uri: str = "",
              response_type: str = "", code_challenge: str = "",
              code_challenge_method: str = "", state: str = "",
              scope: str = "", resource: str = ""):
    """Validate the client's request, then hand the human to Google.

    Errors split into two kinds, and the split is a security property rather than
    tidiness. If the client or the redirect_uri is wrong, the response is rendered here —
    redirecting an error to an unvalidated URI is how an authorization server becomes an
    open redirect. Everything after that is returned to the validated redirect_uri as the
    spec requires, so the client can show the user something useful.
    """
    client = STORE.get_client(client_id) if client_id else None
    if client is None:
        raise _fail(400, "invalid_client", "unknown client_id — register first")
    if not _redirect_ok(redirect_uri, client.redirect_uris):
        raise _fail(400, "invalid_request", "redirect_uri does not match a registered URI")

    def back(error: str, description: str) -> RedirectResponse:
        params = {"error": error, "error_description": description}
        if state:
            params["state"] = state
        return RedirectResponse(f"{redirect_uri}?{urllib.parse.urlencode(params)}", 302)

    if response_type != "code":
        return back("unsupported_response_type", "only response_type=code is supported")
    if code_challenge_method != "S256" or not code_challenge:
        # Downgrade to `plain` defeats the point of PKCE, so it is not offered at all.
        return back("invalid_request", "PKCE with code_challenge_method=S256 is required")
    target = (resource or "").rstrip("/")
    if not target:
        return back("invalid_target", "the resource parameter is required (RFC 8707)")
    if target not in ALLOWED_RESOURCES:
        return back("invalid_target", "unknown resource")
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return back("temporarily_unavailable", "the identity provider is not configured")
    if not (ALLOWED_DOMAINS or ALLOWED_EMAILS):
        # Fail closed and say so, rather than sending a person through a full sign-in
        # that was always going to be refused.
        return back("access_denied", "no identities are permitted on this deployment")

    granted = " ".join(s for s in (scope or "").split() if s in SCOPES) or SCOPES[0]
    nonce = secrets.token_urlsafe(16)
    _PENDING[nonce] = {"client_id": client_id, "redirect_uri": redirect_uri,
                       "code_challenge": code_challenge, "state": state,
                       "resource": target, "scope": granted,
                       "expires_at": time.time() + _PENDING_TTL}
    for key, pending in list(_PENDING.items()):
        if pending["expires_at"] < time.time():
            _PENDING.pop(key, None)

    google = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{_issuer(request)}/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": nonce,
        "prompt": "select_account",
    }
    if ALLOWED_DOMAINS and len(ALLOWED_DOMAINS) == 1:
        # A hint only — the account chooser is friendlier. The claim is still verified.
        google["hd"] = next(iter(ALLOWED_DOMAINS))
    return RedirectResponse(f"{GOOGLE_AUTH}?{urllib.parse.urlencode(google)}", 302)


@app.get("/callback", tags=["oauth"])
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google has authenticated the human. Decide whether they are allowed in."""
    pending = _PENDING.pop(state, None)
    if pending is None or pending["expires_at"] < time.time():
        # No validated redirect_uri to send them back to, so this is rendered here.
        raise _fail(400, "invalid_request", "unknown or expired authorization state")

    def back(params: dict) -> RedirectResponse:
        if pending["state"]:
            params["state"] = pending["state"]
        return RedirectResponse(
            f"{pending['redirect_uri']}?{urllib.parse.urlencode(params)}", 302)

    if error or not code:
        return back({"error": "access_denied",
                     "error_description": error or "no authorization code returned"})

    import requests as http
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token as gid

    try:
        resp = http.post(GOOGLE_TOKEN, timeout=20, data={
            "code": code, "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{_issuer(request)}/callback",
            "grant_type": "authorization_code"})
        raw_id = (resp.json() or {}).get("id_token", "") if resp.ok else ""
        claims = gid.verify_oauth2_token(raw_id, greq.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        return back({"error": "access_denied",
                     "error_description": "could not verify the Google identity"})

    email = (claims.get("email") or "").lower()
    domain = (claims.get("hd") or email.rpartition("@")[2]).lower()
    if not claims.get("email_verified"):
        return back({"error": "access_denied", "error_description": "email not verified"})
    if email not in ALLOWED_EMAILS and domain not in ALLOWED_DOMAINS:
        # The org is the access policy. No allow-list to maintain, and an account that
        # leaves the domain loses access without anyone editing a file.
        return back({"error": "access_denied",
                     "error_description": "this identity is not permitted"})

    record = oauth.Code(
        code=secrets.token_urlsafe(32), client_id=pending["client_id"],
        redirect_uri=pending["redirect_uri"], code_challenge=pending["code_challenge"],
        email=email, subject=claims.get("sub", email), resource=pending["resource"],
        scope=pending["scope"], expires_at=time.time() + oauth.CODE_TTL)
    STORE.put_code(record)
    return back({"code": record.code})


# --- token -------------------------------------------------------------------
@app.post("/token", tags=["oauth"])
def token(request: Request, grant_type: str = Form(""), code: str = Form(""),
          redirect_uri: str = Form(""), client_id: str = Form(""),
          code_verifier: str = Form(""), refresh_token: str = Form(""),
          resource: str = Form("")):
    issuer = _issuer(request)

    if grant_type == "authorization_code":
        record = STORE.take_code(code) if code else None
        if record is None:
            # Deliberately identical for "never existed", "already used" and "expired".
            # Distinguishing them tells an attacker which codes are real.
            raise _fail(400, "invalid_grant", "authorization code is not valid")
        if record.client_id != client_id or record.redirect_uri != redirect_uri:
            raise _fail(400, "invalid_grant", "authorization code is not valid")
        if not code_verifier or oauth.s256(code_verifier) != record.code_challenge:
            raise _fail(400, "invalid_grant", "PKCE verification failed")

        access, ttl = oauth.access_token(
            KEY, issuer=issuer, subject=record.subject, email=record.email,
            audience=record.resource, client_id=record.client_id, scope=record.scope)
        refresh = secrets.token_urlsafe(32)
        STORE.put_refresh(refresh, {
            "client_id": record.client_id, "email": record.email,
            "subject": record.subject, "resource": record.resource,
            "scope": record.scope, "expires_at": time.time() + oauth.REFRESH_TTL})
        return {"access_token": access, "token_type": "Bearer", "expires_in": ttl,
                "refresh_token": refresh, "scope": record.scope}

    if grant_type == "refresh_token":
        # Rotating: the presented token is consumed whether or not this succeeds, so a
        # stolen refresh token is usable at most once, and the legitimate client's next
        # refresh fails loudly instead of the theft being silent.
        held = STORE.take_refresh(refresh_token) if refresh_token else None
        if held is None or held.get("client_id") != client_id:
            raise _fail(400, "invalid_grant", "refresh token is not valid")
        target = (resource or held["resource"]).rstrip("/")
        if target != held["resource"]:
            # A refresh must not widen reach to a resource the human never approved.
            raise _fail(400, "invalid_target", "resource does not match the grant")

        access, ttl = oauth.access_token(
            KEY, issuer=issuer, subject=held["subject"], email=held["email"],
            audience=held["resource"], client_id=client_id, scope=held["scope"])
        rotated = secrets.token_urlsafe(32)
        STORE.put_refresh(rotated, {**held,
                                    "expires_at": time.time() + oauth.REFRESH_TTL})
        return {"access_token": access, "token_type": "Bearer", "expires_in": ttl,
                "refresh_token": rotated, "scope": held["scope"]}

    raise _fail(400, "unsupported_grant_type", f"{grant_type!r} is not supported")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8092")))
