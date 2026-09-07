"""Resource-server token validation for the MCP endpoint (ADR-0020).

ADR-0020 puts the OAuth proxy in the *authorization* path and keeps it out of the *data*
path: a client gets a token from the proxy, then talks to this server directly. So this
server has to validate the token itself, from nothing but the proxy's published JWKS.

Everything here fails closed, and the two rules that matter most are the two most often
skipped:

  * **The signature is verified against a fetched key, and `alg` is pinned to RS256.**
    Accepting the token's own `alg` header is the `alg: none` family of bypass. The
    algorithm a server accepts is a property of the server, never of the token.
  * **The audience must be this resource.** RFC 8707 exists because an issuer usually
    serves several resources, and a token minted for a lower-value one must not be
    replayable here. Without an audience check, "a valid token" means "a token from
    anyone this issuer will talk to".

Enforcement is off unless `FINCHAT_MCP_OAUTH_ISSUER` and `FINCHAT_MCP_RESOURCE` are both
set. That is not a permissive default: the deployed service is private and Cloud Run IAM
is the control, so with OAuth unconfigured there are two locks rather than none. Turning
this on is what lets the service become public — and `require_auth()` says so, rather than
leaving the pairing to a runbook.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

ISSUER = os.getenv("FINCHAT_MCP_OAUTH_ISSUER", "").rstrip("/")
# Service accounts allowed to call as themselves, with a Google-issued OIDC token
# audienced to this service. Empty means no service may call — the same fail-closed
# default as everything else here, and it matters more once the service is public,
# because Cloud Run is no longer checking anything on the way in.
# Separated by ';' OR ',', and tolerant of both on purpose. `gcloud run deploy
# --set-env-vars` uses the comma as ITS delimiter, so a comma-separated list of emails
# is parsed as separate variables and the deploy fails with "Bad syntax for dict arg" —
# which names the second email and not the reason. The deploy therefore passes ';', and
# accepting ',' too means the next person to write one by hand is not caught by a rule
# they had no way to know.
SERVICE_CALLERS = {e.strip().lower() for e in
                   re.split(r"[;,]", os.getenv("FINCHAT_MCP_SERVICE_CALLERS", ""))
                   if e.strip()}
RESOURCE = os.getenv("FINCHAT_MCP_RESOURCE", "").rstrip("/")
# The audience Google mints for a service-to-service call: the service URL, with
# no path. RESOURCE carries the /mcp path, which is what OAuth clients ask for.
SERVICE_URL = RESOURCE[:-len("/mcp")] if RESOURCE.endswith("/mcp") else RESOURCE
JWKS_TTL = int(os.getenv("FINCHAT_MCP_JWKS_TTL", "3600"))
LEEWAY = 60  # clock skew between two Cloud Run services

_jwks: tuple[float, dict] | None = None


class Unauthorized(Exception):
    """The caller gets a 401 and a pointer to the authorization server. Nothing else.

    The `detail` is for logs. A 401 body that explains *why* a token failed tells an
    attacker which of their guesses was closer.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def enabled() -> bool:
    return bool(ISSUER and RESOURCE)


def protected_resource_metadata() -> dict:
    """RFC 9728. How a client discovers which authorization server to go to.

    A hosted client that gets a 401 reads this to find the issuer, registers itself there
    and runs the flow. Without it the client has a token endpoint it cannot find and the
    connection simply fails.
    """
    return {"resource": RESOURCE,
            "authorization_servers": [ISSUER],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp:tools", "mcp:resources"]}


def challenge() -> str:
    """The `WWW-Authenticate` header a 401 must carry, naming the metadata document."""
    return (f'Bearer resource_metadata="{RESOURCE}/.well-known/oauth-protected-resource"'
            if enabled() else "Bearer")


def _keys(force: bool = False) -> dict:
    global _jwks
    if _jwks and not force and _jwks[0] > time.time():
        return _jwks[1]
    url = f"{ISSUER}/.well-known/jwks.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            body = json.loads(response.read().decode())
    except Exception as exc:
        # Unauthorized, not the transport error. The caller catches Unauthorized and
        # answers 401; anything else escapes as a 500, so an authorization server that
        # is briefly unreachable would turn every request into a server error instead
        # of a refusal — and take the service's own health signal down with it.
        raise Unauthorized(f"jwks unavailable: {type(exc).__name__}") from None
    keys = {k["kid"]: k for k in body.get("keys", []) if k.get("kid")}
    if not keys:
        raise Unauthorized("authorization server published no usable keys")
    _jwks = (time.time() + JWKS_TTL, keys)
    return keys


def _verify_google_service(token: str) -> dict:
    """A Google-issued OIDC token from a named service account, audienced to us.

    This is what Cloud Run IAM validates when the service is private. Once it is public
    Cloud Run validates nothing, so the same check has to happen here or the in-GCP
    callers that were never going through the OAuth proxy — the AI gateway, the BFF —
    stop working the moment the door opens.

    Strictness is the whole thing. Verifying a Google signature and stopping there
    accepts a token minted for ANY service by ANY Google identity, which is close to no
    check at all. Three things are load-bearing and each is mutation-verified: the
    audience must be this exact service, the email must be one we named, and it must be
    verified.
    """
    # An early exit, not the control — mutation testing showed removing it changes
    # nothing, because the membership check below refuses against an empty set anyway.
    # It stays to avoid fetching Google's certificates on a request that cannot succeed.
    if not SERVICE_CALLERS:
        raise Unauthorized("no service callers are permitted")

    from google.auth.transport import requests as greq
    from google.oauth2 import id_token as gid

    try:
        claims = gid.verify_oauth2_token(token, greq.Request(), audience=SERVICE_URL)
    except Exception as exc:
        raise Unauthorized(f"google: {type(exc).__name__}") from None

    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified") or email not in SERVICE_CALLERS:
        raise Unauthorized("service identity is not permitted")
    return {**claims, "kind": "service"}


def verify(token: str) -> dict:
    """Return the claims of a valid access token, or raise Unauthorized.

    Two kinds of caller reach this server and they authenticate differently:

      * a **person**, via the OAuth proxy (ADR-0020) — a token this platform minted;
      * a **service** inside GCP, via a Google OIDC token audienced to this service.

    Both are checked here rather than one of them being left to Cloud Run IAM, because
    a public service has no Cloud Run IAM. Which one succeeded is recorded, since
    "a person asked" and "a service asked on nobody's behalf" are different facts about
    the same request and the audit should not blur them.
    """
    if not enabled():
        raise Unauthorized("OAuth is not configured on this server")
    if not token:
        raise Unauthorized("no bearer token")

    import jwt
    from jwt import PyJWK

    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        raise Unauthorized("malformed token") from None

    # Route on the CLAIMED issuer, then verify properly for that route. Reading an
    # unverified claim to choose a verifier is safe; using one to make a decision is not.
    if str(unverified.get("iss", "")).rstrip("/") != ISSUER:
        return _verify_google_service(token)

    kid = header.get("kid")
    if not kid:
        raise Unauthorized("token has no key id")

    keys = _keys()
    if kid not in keys:
        # One forced refresh, then give up. A key rotation should not need a redeploy;
        # an unknown kid on every request must not become an unauthenticated fetch of a
        # remote URL per request either.
        keys = _keys(force=True)
    if kid not in keys:
        raise Unauthorized("token signed by an unknown key")

    try:
        claims = jwt.decode(
            token,
            PyJWK.from_dict(keys[kid]).key,
            algorithms=["RS256"],   # pinned here, never read from the token
            audience=RESOURCE,      # RFC 8707
            issuer=ISSUER,
            leeway=LEEWAY,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except Exception as exc:
        raise Unauthorized(f"{type(exc).__name__}") from None

    if not claims.get("email"):
        # The whole point of ADR-0020 is that a call is bound to a person. A token that
        # validates but names nobody defeats it, and would make the audit useless.
        raise Unauthorized("token carries no identity")
    return {**claims, "kind": "user"}
