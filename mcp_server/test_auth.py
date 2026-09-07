"""Resource-server validation tests (ADR-0020).

The proxy decides who gets a token. This decides which tokens are honoured, and it is the
half an attacker actually reaches: the authorization server can be perfect and it buys
nothing if the resource server accepts a token signed by someone else, minted for another
audience, or carrying `alg: none`.

Offline: tokens are signed with a locally generated key and the JWKS fetch is stubbed, so
this never contacts a running proxy.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ISSUER = "https://auth.example"
RESOURCE = "https://mcp.example/mcp"


def _load(name: str, filename: str):
    """By path, under a unique name — `mcp_server/` and `ui/` both contain a
    `server.py`, and `import auth` in a whole-repo run is no safer."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def auth(monkeypatch):
    monkeypatch.setenv("FINCHAT_MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("FINCHAT_MCP_RESOURCE", RESOURCE)
    module = _load("finchat_mcp_auth_rs", "auth.py")
    return module


@pytest.fixture(scope="module")
def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    numbers = private.public_key().public_numbers()
    import base64

    def b64u(i: int) -> str:
        raw = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test-kid",
           "n": b64u(numbers.n), "e": b64u(numbers.e)}
    return pem, jwk


@pytest.fixture()
def signed(auth, keypair, monkeypatch):
    pem, jwk = keypair
    monkeypatch.setattr(auth, "_jwks", (time.time() + 3600, {jwk["kid"]: jwk}))

    def make(**over):
        import jwt

        now = int(time.time())
        claims = {"iss": ISSUER, "sub": "google-123", "aud": RESOURCE,
                  "email": "staff@bank.example", "client_id": "mcp-abc",
                  "scope": "mcp:tools", "iat": now, "exp": now + 900,
                  "jti": "t1"}
        claims.update({k: v for k, v in over.items() if v is not None})
        for key, value in over.items():
            if value is None:
                claims.pop(key, None)
        return jwt.encode(claims, pem, algorithm="RS256",
                          headers={"kid": over.get("kid", jwk["kid"])})

    return make


def test_a_well_formed_token_is_accepted(auth, signed):
    claims = auth.verify(signed())
    assert claims["email"] == "staff@bank.example"


def test_a_token_for_another_audience_is_refused(auth, signed):
    """RFC 8707's whole reason. An issuer usually serves several resources, and a token
    minted for a lower-value one must not be replayable against banking tools."""
    with pytest.raises(auth.Unauthorized):
        auth.verify(signed(aud="https://some-other-service.example"))


def test_a_token_from_another_issuer_is_refused(auth, signed):
    with pytest.raises(auth.Unauthorized):
        auth.verify(signed(iss="https://attacker.example"))


def test_an_expired_token_is_refused(auth, signed):
    now = int(time.time())
    with pytest.raises(auth.Unauthorized):
        auth.verify(signed(iat=now - 7200, exp=now - 3600))


def test_a_token_signed_by_a_different_key_is_refused(auth, signed, keypair):
    """The kid matches a published key; the signature does not. Verifying that the header
    names a known key without checking the signature against it is a real mistake, and it
    accepts anything."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    now = int(time.time())
    forged = jwt.encode({"iss": ISSUER, "sub": "x", "aud": RESOURCE,
                         "email": "attacker@bank.example", "iat": now, "exp": now + 900},
                        pem, algorithm="RS256", headers={"kid": keypair[1]["kid"]})
    with pytest.raises(auth.Unauthorized):
        auth.verify(forged)


def test_an_unsigned_token_is_refused(auth, signed, monkeypatch):
    """`alg: none`. The algorithm a server accepts is a property of the server; reading it
    from the token is the oldest JWT bypass there is."""
    import jwt

    now = int(time.time())
    unsigned = jwt.encode({"iss": ISSUER, "sub": "x", "aud": RESOURCE,
                           "email": "attacker@bank.example", "iat": now, "exp": now + 900},
                          key="", algorithm="none", headers={"kid": "test-kid"})
    with pytest.raises(auth.Unauthorized):
        auth.verify(unsigned)


def test_algorithm_confusion_is_refused(auth, keypair, monkeypatch):
    """The attack the `algorithms=["RS256"]` pin actually stops.

    An RSA public key is public. If the server derives the algorithm from the token's own
    header, an attacker signs `alg: HS256` using that public key as the HMAC secret, and
    the server — holding the same public key — verifies it happily. The token is forged
    and valid at once.

    Worth being exact about what this proves, because mutation testing showed the
    obvious claim is wrong: deriving the algorithm from the token's header does NOT make
    this test fail. PyJWT refuses an RSA key object as an HMAC secret on the verifying
    side too, so the library stops the attack whether or not this server pins anything.

    The test stays, and so does the pin. The test pins the OUTCOME — a forged HS256
    token is rejected — which is the property that must survive a library swap or a
    PyJWT release that relaxes that guard. The pin stays because relying on a
    dependency's internal check for a security property of this server is how the
    property disappears in a version bump nobody reads.
    """
    import base64
    import hashlib
    import hmac
    import json as _json

    from jwt import PyJWK
    from cryptography.hazmat.primitives import serialization

    _, jwk = keypair
    monkeypatch.setattr(auth, "_jwks", (time.time() + 3600, {jwk["kid"]: jwk}))

    public_pem = PyJWK.from_dict(jwk).key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)

    def seg(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = seg(_json.dumps({"alg": "HS256", "typ": "JWT",
                              "kid": jwk["kid"]}).encode())
    payload = seg(_json.dumps({"iss": ISSUER, "sub": "attacker", "aud": RESOURCE,
                               "email": "attacker@bank.example",
                               "iat": now, "exp": now + 900}).encode())
    # Hand-rolled, because PyJWT refuses to *encode* this — it guards the signing side.
    # The attack targets the verifying side, which is what is under test.
    signature = seg(hmac.new(public_pem, header + b"." + payload,
                             hashlib.sha256).digest())
    forged = b".".join([header, payload, signature]).decode()

    with pytest.raises(auth.Unauthorized):
        auth.verify(forged)


def test_a_token_naming_nobody_is_refused(auth, signed):
    """ADR-0020 exists to bind a call to a person. A token that validates and names none
    defeats the point and makes the audit trail a list of nulls."""
    with pytest.raises(auth.Unauthorized):
        auth.verify(signed(email=None))


def test_required_claims_are_required(auth, signed):
    for missing in ("exp", "iat", "sub"):
        with pytest.raises(auth.Unauthorized):
            auth.verify(signed(**{missing: None}))


def test_a_missing_or_malformed_token_is_refused(auth):
    for bad in ("", "not-a-jwt", "a.b", "..", "Bearer x"):
        with pytest.raises(auth.Unauthorized):
            auth.verify(bad)


def test_an_unknown_key_id_triggers_exactly_one_refresh(auth, signed, keypair,
                                                        monkeypatch):
    """A rotated key must not need a redeploy. An unknown kid on every request must not
    become an unauthenticated outbound fetch per request either."""
    calls = []
    _, jwk = keypair

    def fake_keys(force=False):
        calls.append(force)
        return {jwk["kid"]: jwk} if force else {}

    monkeypatch.setattr(auth, "_keys", fake_keys)
    assert auth.verify(signed())["email"] == "staff@bank.example"
    assert calls == [False, True]


def test_validation_is_off_and_refuses_when_unconfigured(monkeypatch):
    """With no issuer or resource the deployed service is private and Cloud Run IAM is
    the control. `verify` must still refuse rather than wave a token through — the two
    are locks in series, not alternatives."""
    monkeypatch.delenv("FINCHAT_MCP_OAUTH_ISSUER", raising=False)
    monkeypatch.delenv("FINCHAT_MCP_RESOURCE", raising=False)
    module = _load("finchat_mcp_auth_rs_off", "auth.py")
    assert module.enabled() is False
    with pytest.raises(module.Unauthorized):
        module.verify("anything")


def test_the_metadata_document_points_at_the_authorization_server(auth):
    """RFC 9728. A client that gets a 401 reads this to find where to register; without
    it the connection just fails."""
    body = auth.protected_resource_metadata()
    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]
    assert "resource_metadata=" in auth.challenge()


def test_the_failure_reason_never_reaches_the_caller(auth, signed):
    """Distinguishing "expired" from "wrong audience" tells an attacker which guess was
    closer. The detail is for the log; the caller gets `invalid_token`."""
    reasons = set()
    for token in (signed(aud="https://elsewhere.example"), signed(exp=1), "not-a-jwt"):
        try:
            auth.verify(token)
        except auth.Unauthorized as exc:
            reasons.add(exc.detail)
    assert len(reasons) > 1, "details should differ in the log"
    assert json.dumps({"error": "invalid_token"}) == '{"error": "invalid_token"}'
