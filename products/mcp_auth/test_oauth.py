"""Security tests for the MCP OAuth proxy.

Every test here is an attack that works against a plausible implementation of this
service. None of them is hypothetical: PKCE downgrade, code replay, redirect-URI prefix
matching, cross-client code use, audience widening on refresh, and an unset allow-list
read as "allow everyone" are the recurring ways an authorization server is got wrong.

Runs fully offline. Google is never contacted — `/callback` is exercised by injecting the
verified claims, because what is under test is what this service decides once an identity
is known, not whether Google's library verifies a signature.
"""
import importlib.util
import os
import sys
import time
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.update({
    "GOOGLE_OAUTH_CLIENT_ID": "test-google-client",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-google-secret",
    "OAUTH_ALLOWED_DOMAINS": "bank.example",
    "OAUTH_ALLOWED_EMAILS": "auditor@gmail.example",
    "OAUTH_ALLOWED_RESOURCES": "https://mcp.example/mcp",
    "OAUTH_ISSUER": "https://auth.example",
})


def _load(name: str, filename: str):
    """Load by path under a unique name: every service in this repo has a `main.py`, and
    a whole-repo pytest run otherwise resolves `import main` to whichever loaded first."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


oauth = _load("finchat_mcp_oauth", "oauth.py")
main = _load("finchat_mcp_auth_main", "main.py")
client = TestClient(main.app, follow_redirects=False)

RESOURCE = "https://mcp.example/mcp"
VERIFIER = "a-high-entropy-code-verifier-value-of-sufficient-length"
CHALLENGE = oauth.s256(VERIFIER)


def register(uris=("https://app.example/cb",)) -> str:
    r = client.post("/register", json={"client_name": "test", "redirect_uris": list(uris)})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authorize(client_id, redirect_uri="https://app.example/cb", **over):
    params = {"client_id": client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "code_challenge": CHALLENGE,
              "code_challenge_method": "S256", "state": "xyz",
              "resource": RESOURCE, "scope": "mcp:tools"}
    params.update(over)
    return client.get("/authorize", params=params)


def complete_login(authorize_response, email="staff@bank.example", hd="bank.example",
                   verified=True):
    """Drive /callback with Google's answer already verified, as the real flow would."""
    nonce = urllib.parse.parse_qs(
        urllib.parse.urlparse(authorize_response.headers["location"]).query)["state"][0]

    class _Claims(dict):
        pass

    def fake_verify(raw, request, audience):
        return {"email": email, "email_verified": verified, "hd": hd, "sub": "google-123"}

    class _Resp:
        ok = True

        @staticmethod
        def json():
            return {"id_token": "irrelevant-because-verification-is-stubbed"}

    import google.oauth2.id_token as gid
    import requests as http
    old_verify, old_post = gid.verify_oauth2_token, http.post
    gid.verify_oauth2_token = fake_verify
    http.post = lambda *a, **k: _Resp()
    try:
        return client.get("/callback", params={"code": "google-code", "state": nonce})
    finally:
        gid.verify_oauth2_token, http.post = old_verify, old_post


def code_from(response) -> str:
    return urllib.parse.parse_qs(
        urllib.parse.urlparse(response.headers["location"]).query)["code"][0]


def exchange(client_id, code, verifier=VERIFIER, redirect_uri="https://app.example/cb"):
    return client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": redirect_uri, "code_verifier": verifier})


# --- the happy path, so the refusals below mean something --------------------
def test_a_registered_client_and_a_permitted_human_get_a_token():
    cid = register()
    token = exchange(cid, code_from(complete_login(authorize(cid))))
    assert token.status_code == 200, token.text
    body = token.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == oauth.ACCESS_TTL
    assert body["refresh_token"]

    import jwt
    claims = jwt.decode(body["access_token"], options={"verify_signature": False},
                        audience=RESOURCE)
    assert claims["aud"] == RESOURCE          # RFC 8707: one resource, named up front
    assert claims["email"] == "staff@bank.example"
    assert claims["iss"] == "https://auth.example"
    assert claims["exp"] - claims["iat"] == oauth.ACCESS_TTL


def test_the_issued_token_verifies_against_the_published_jwks():
    """The resource server has nothing but the JWKS. If it cannot verify with that, the
    token is decoration."""
    import jwt
    from jwt import PyJWK

    cid = register()
    access = exchange(cid, code_from(complete_login(authorize(cid)))).json()["access_token"]
    keys = client.get("/.well-known/jwks.json").json()["keys"]
    kid = jwt.get_unverified_header(access)["kid"]
    key = PyJWK.from_dict(next(k for k in keys if k["kid"] == kid))
    claims = jwt.decode(access, key.key, algorithms=["RS256"], audience=RESOURCE,
                        issuer="https://auth.example")
    assert claims["email"] == "staff@bank.example"


# --- PKCE ---------------------------------------------------------------------
def test_pkce_is_required_and_plain_is_not_offered():
    """`plain` makes the challenge equal the verifier, so an attacker who intercepted the
    authorization request can complete the exchange. Offering it as a fallback is the
    same as not doing PKCE."""
    cid = register()
    assert "plain" not in client.get(
        "/.well-known/oauth-authorization-server").json()["code_challenge_methods_supported"]
    for over in ({"code_challenge_method": "plain"}, {"code_challenge": ""},
                 {"code_challenge_method": ""}):
        located = authorize(cid, **over).headers["location"]
        assert "error=invalid_request" in located, over


def test_the_wrong_verifier_does_not_redeem_the_code():
    cid = register()
    code = code_from(complete_login(authorize(cid)))
    assert exchange(cid, code, verifier="not-the-verifier").status_code == 400


# --- authorization codes ------------------------------------------------------
def test_a_code_cannot_be_replayed():
    """The classic. An intercepted redirect is worth nothing if the code is already spent
    — and single-use has to be a property of the fetch, not a flag checked afterwards."""
    cid = register()
    code = code_from(complete_login(authorize(cid)))
    assert exchange(cid, code).status_code == 200
    assert exchange(cid, code).status_code == 400


def test_a_code_expires():
    cid = register()
    code = code_from(complete_login(authorize(cid)))
    record = main.STORE._codes[code]
    record.expires_at = time.time() - 1
    assert exchange(cid, code).status_code == 400


def test_one_client_cannot_redeem_another_clients_code():
    victim, attacker = register(), register(("https://evil.example/cb",))
    code = code_from(complete_login(authorize(victim)))
    assert exchange(attacker, code, redirect_uri="https://evil.example/cb").status_code == 400
    # And the failed attempt must not have burned the legitimate client's code silently
    # in a way that leaves the attacker informed and the victim broken... it IS consumed,
    # which is the safe direction: fail closed, and the victim retries.
    assert exchange(victim, code).status_code == 400


def test_the_redirect_uri_must_match_the_one_the_code_was_issued_for():
    cid = register(("https://app.example/cb", "https://app.example/other"))
    code = code_from(complete_login(authorize(cid, redirect_uri="https://app.example/cb")))
    assert exchange(cid, code, redirect_uri="https://app.example/other").status_code == 400


# --- redirect URIs ------------------------------------------------------------
def test_redirect_uris_match_exactly_and_never_by_prefix():
    """Prefix matching is the usual mistake, and it hands the code to the attacker:
    a registered `https://app.example/cb` would also permit `.../cb.evil.example`."""
    cid = register(("https://app.example/cb",))
    for evil in ("https://app.example/cb.evil.example", "https://app.example/cb/../..",
                 "https://app.example/cb?x=1", "https://app.example/cbb",
                 "https://evil.example/cb"):
        assert authorize(cid, redirect_uri=evil).status_code == 400, evil


def test_an_invalid_redirect_is_rendered_not_redirected():
    """Redirecting an error to an unvalidated URI turns an authorization server into an
    open redirect — a phishing primitive on a bank's own domain."""
    cid = register()
    response = authorize(cid, redirect_uri="https://evil.example/cb")
    assert response.status_code == 400
    assert "location" not in {h.lower() for h in response.headers}


def test_registration_refuses_unusable_redirect_targets():
    for evil in (["http://evil.example/cb"], ["https://app.example/cb#frag"],
                 ["ftp://app.example/cb"], [], "not-a-list"):
        r = client.post("/register", json={"client_name": "x", "redirect_uris": evil})
        assert r.status_code == 400, evil


def test_loopback_http_is_allowed_for_desktop_clients():
    """RFC 8252. A native client has no https origin, and refusing it would exclude every
    desktop MCP client rather than making anything safer."""
    assert client.post("/register", json={
        "client_name": "desktop",
        "redirect_uris": ["http://127.0.0.1:47821/cb"]}).status_code == 201


# --- who is allowed in --------------------------------------------------------
def test_an_identity_outside_the_domain_is_refused():
    cid = register()
    located = complete_login(authorize(cid), email="someone@elsewhere.example",
                             hd="elsewhere.example").headers["location"]
    assert "error=access_denied" in located
    assert "code=" not in located


def test_an_unverified_email_is_refused():
    cid = register()
    located = complete_login(authorize(cid), verified=False).headers["location"]
    assert "error=access_denied" in located


def test_an_explicitly_allowed_email_outside_the_domain_is_admitted():
    """The domain is the policy; the email list is the documented exception for personas
    that legitimately sit outside the Workspace org."""
    cid = register()
    located = complete_login(authorize(cid), email="auditor@gmail.example",
                             hd=None).headers["location"]
    assert "code=" in located


def test_an_email_allow_list_alone_does_not_admit_everyone(monkeypatch):
    """Found by mutation testing, not by writing it out.

    The `/authorize` guard refuses when BOTH lists are empty, which hid a hole in the
    `/callback` check: an implementation reading "if a domain list exists, enforce it"
    passes every test above, because every test configures a domain. Configure only an
    email list — a legitimate deployment, for personas outside the Workspace org — and
    that implementation admits any identity Google will authenticate, which is anyone
    with a Google account.
    """
    monkeypatch.setattr(main, "ALLOWED_DOMAINS", set())
    monkeypatch.setattr(main, "ALLOWED_EMAILS", {"auditor@gmail.example"})
    cid = register()
    located = complete_login(authorize(cid), email="stranger@anywhere.example",
                             hd="anywhere.example").headers["location"]
    assert "error=access_denied" in located
    assert "code=" not in located


def test_no_allow_list_means_nobody_rather_than_everybody(monkeypatch):
    """The single most important default in this file. An unconfigured deployment must
    refuse, and must refuse BEFORE sending a person through a sign-in that was always
    going to fail."""
    monkeypatch.setattr(main, "ALLOWED_DOMAINS", set())
    monkeypatch.setattr(main, "ALLOWED_EMAILS", set())
    cid = register()
    located = authorize(cid).headers["location"]
    assert "error=access_denied" in located
    assert "accounts.google.com" not in located


# --- resource indicators (RFC 8707) -------------------------------------------
def test_a_token_cannot_be_minted_for_an_unlisted_resource():
    """Without this, any client could ask for a token whose audience is some other
    service that also trusts this issuer, and this server would sign it."""
    cid = register()
    assert "error=invalid_target" in authorize(cid, resource="https://other.example/mcp"
                                               ).headers["location"]
    assert "error=invalid_target" in authorize(cid, resource="").headers["location"]


def test_a_refresh_cannot_widen_the_audience():
    cid = register()
    refresh = exchange(cid, code_from(complete_login(authorize(cid)))).json()["refresh_token"]
    r = client.post("/token", data={"grant_type": "refresh_token", "client_id": cid,
                                    "refresh_token": refresh,
                                    "resource": "https://other.example/mcp"})
    assert r.status_code == 400


# --- refresh tokens -----------------------------------------------------------
def test_refresh_tokens_rotate_and_the_old_one_dies():
    """A stolen refresh token is then usable at most once, and the legitimate client's
    next refresh fails loudly instead of the theft being silent."""
    cid = register()
    first = exchange(cid, code_from(complete_login(authorize(cid)))).json()["refresh_token"]
    second = client.post("/token", data={"grant_type": "refresh_token",
                                         "client_id": cid, "refresh_token": first})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first
    assert client.post("/token", data={"grant_type": "refresh_token", "client_id": cid,
                                       "refresh_token": first}).status_code == 400


def test_a_refresh_token_is_bound_to_its_client():
    cid, other = register(), register()
    refresh = exchange(cid, code_from(complete_login(authorize(cid)))).json()["refresh_token"]
    assert client.post("/token", data={"grant_type": "refresh_token", "client_id": other,
                                       "refresh_token": refresh}).status_code == 400


def test_refresh_tokens_are_not_stored_in_the_clear():
    """The store is the one place every long-lived credential sits together. Reading it
    should not produce a set of working tokens."""
    cid = register()
    refresh = exchange(cid, code_from(complete_login(authorize(cid)))).json()["refresh_token"]
    assert refresh not in main.STORE._refresh
    assert any(len(k) == 64 for k in main.STORE._refresh)  # sha256 hex


# --- misc ---------------------------------------------------------------------
def test_an_unknown_client_gets_no_flow_at_all():
    assert authorize("mcp-never-registered").status_code == 400


def test_unsupported_grants_are_refused():
    assert client.post("/token", data={"grant_type": "password",
                                       "username": "a", "password": "b"}).status_code == 400


def test_metadata_reports_deployment_faults_that_would_look_intermittent():
    """An ephemeral signing key rejects tokens it issued a moment ago after a cold start;
    a non-durable store cannot redeem a code on a second instance. Both present as
    'sometimes it logs me out', which is the hardest kind of bug to be told about."""
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["x_finchat_signing_key_ephemeral"] is True   # no PEM configured in tests
    assert body["x_finchat_store_durable"] is False
    assert set(body["grant_types_supported"]) == {"authorization_code", "refresh_token"}


def test_a_stable_pem_produces_a_stable_key_id():
    """A redeploy with the same key must not invalidate every cached JWKS."""
    first = oauth.SigningKey()
    assert oauth.SigningKey(first.pem).kid == first.kid
    assert oauth.SigningKey().kid != first.kid
