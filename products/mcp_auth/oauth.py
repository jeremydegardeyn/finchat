"""Keys, storage and tokens for the MCP OAuth proxy (ADR-0020).

Everything here fails closed. An authorization server in front of banking tools is the
one component where a permissive default is not a convenience — it is the vulnerability.
Where a value is unset, the answer is "no", not "everything".

Three concerns, kept in one module because they are meaningless apart: the signing key,
the records a flow needs between redirects, and the tokens themselves.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# --- lifetimes ---------------------------------------------------------------
# Short, because the proxy is not in the data path: a client re-mints cheaply and an
# access token that leaks is useful for minutes rather than days.
ACCESS_TTL = int(os.getenv("OAUTH_ACCESS_TTL", "900"))          # 15 minutes
REFRESH_TTL = int(os.getenv("OAUTH_REFRESH_TTL", "2592000"))    # 30 days
CODE_TTL = int(os.getenv("OAUTH_CODE_TTL", "60"))               # 60 seconds
# An authorization code is exchanged within a second of being issued by any real client.
# A minute is generous; an hour is an invitation.


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def s256(verifier: str) -> str:
    return b64u(hashlib.sha256(verifier.encode("ascii")).digest())


# --- signing key -------------------------------------------------------------
class SigningKey:
    """The RSA key this server signs access tokens with.

    In a deployed environment the PEM arrives from Secret Manager through an env var, so
    the key survives a restart and every instance of a scaled-out service agrees. With
    none configured, one is generated in memory — which is right for a test and wrong for
    a deployment, so `ephemeral` is exposed and the metadata endpoint reports it. A
    server that silently invents a new key on each cold start rejects every token it
    issued a moment ago, and the symptom is "sometimes it logs me out".
    """

    def __init__(self, pem: str | None = None):
        raw = pem or os.getenv("OAUTH_SIGNING_KEY_PEM") or ""
        self.ephemeral = not raw.strip()
        if self.ephemeral:
            self._private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            self._private = serialization.load_pem_private_key(
                raw.encode() if isinstance(raw, str) else raw, password=None)
        self.pem = self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()).decode()
        numbers = self._private.public_key().public_numbers()
        self._n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        self._e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        # Deterministic from the key material, so a redeploy with the same PEM keeps the
        # same kid and cached JWKS stay valid.
        self.kid = hashlib.sha256(self._n).hexdigest()[:16]

    def jwks(self) -> dict:
        return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256",
                          "kid": self.kid, "n": b64u(self._n), "e": b64u(self._e)}]}

    def sign(self, claims: dict) -> str:
        return jwt.encode(claims, self.pem, algorithm="RS256",
                          headers={"kid": self.kid})


# --- records -----------------------------------------------------------------
@dataclass
class Client:
    client_id: str
    client_name: str
    redirect_uris: list[str]
    created_at: float = field(default_factory=time.time)


@dataclass
class Code:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    email: str
    subject: str
    resource: str
    scope: str
    expires_at: float


class Store:
    """Clients, authorization codes and refresh tokens.

    Firestore-backed when configured, in-memory otherwise — the same degradation the rest
    of this platform uses, with one difference that matters: in-memory is correct for a
    test and a single local process, and is a *bug* on a scaled-out deployment, because
    an authorization code issued by one instance cannot be redeemed by another. The
    caller checks `durable` and the metadata endpoint reports it rather than letting that
    present as an intermittent login failure.
    """

    def __init__(self, project: str | None = None, database: str | None = None):
        self.project = project or os.getenv("GCP_PROJECT", "")
        self.database = database or os.getenv("FIRESTORE_DATABASE", "(default)")
        self._clients: dict[str, Client] = {}
        self._codes: dict[str, Code] = {}
        self._refresh: dict[str, dict] = {}
        self._probed: bool | None = None

    @property
    def durable(self) -> bool:
        """Whether the shared store actually WORKS — not whether it is configured.

        The first version of this returned `bool(project)`, which is the same optimistic
        reporting this whole service was built to avoid: the deployment pointed at a
        Firestore database that is in DATASTORE mode, every write raised, every exception
        was swallowed into the in-memory fallback, and the metadata endpoint cheerfully
        said `durable: true`. A health signal that reports intent rather than outcome is
        worse than none, because it is believed.

        So it writes and reads back once, and caches the answer. Once, because a probe
        per metadata fetch would bill and stall a discovery endpoint that clients poll.
        """
        if self._probed is not None:
            return self._probed
        self._probed = False
        if self.project:
            try:
                ref = self._db().collection("mcp_oauth_probe").document("startup")
                ref.set({"at": time.time()})
                self._probed = ref.get().exists
            except Exception as exc:
                print(f"mcp-auth: store is NOT durable — {type(exc).__name__}: {exc}")
        return self._probed

    def _db(self):
        from google.cloud import firestore
        return firestore.Client(project=self.project, database=self.database)

    # -- clients --
    def put_client(self, client: Client) -> None:
        self._clients[client.client_id] = client
        if not self.durable:
            return
        try:
            self._db().collection("mcp_oauth_clients").document(client.client_id).set({
                "client_name": client.client_name,
                "redirect_uris": client.redirect_uris,
                "created_at": client.created_at,
            })
        except Exception:
            pass  # the in-memory copy still serves this instance

    def get_client(self, client_id: str) -> Client | None:
        hit = self._clients.get(client_id)
        if hit or not self.durable:
            return hit
        try:
            snap = self._db().collection("mcp_oauth_clients").document(client_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            client = Client(client_id, data.get("client_name", ""),
                            list(data.get("redirect_uris") or []),
                            float(data.get("created_at") or 0))
            self._clients[client_id] = client
            return client
        except Exception:
            return None

    def prune_probe_clients(self, name: str, older_than: float) -> int:
        """Delete stale registrations made by an automated checker.

        The live verifier registers a client on every run, and it runs on a schedule, so
        the collection would grow forever. Each record grants nothing on its own — a
        client_id is an identifier, and a token still needs a human — so this is
        housekeeping rather than a control. It is here anyway: unbounded growth driven by
        a cron is fine until it is not, and bounding it costs ten lines.

        Matched on the exact `client_name` the checker uses AND on age. A real client
        never carries that name, so nothing a person registered is reachable from here.
        """
        cutoff = time.time() - older_than
        removed = 0
        for client_id, client in list(self._clients.items()):
            if client.client_name == name and client.created_at < cutoff:
                self._clients.pop(client_id, None)
                removed += 1
        if not self.durable:
            return removed
        try:
            collection = self._db().collection("mcp_oauth_clients")
            # Equality on one field only: a compound filter would need a composite index,
            # and an authorization server that 500s because an index is missing is a
            # worse outcome than reading fifty documents.
            for snap in collection.where("client_name", "==", name).limit(50).stream():
                if float((snap.to_dict() or {}).get("created_at") or 0) < cutoff:
                    snap.reference.delete()
                    removed += 1
        except Exception as exc:
            print(f"mcp-auth: could not prune probe clients — {type(exc).__name__}: {exc}")
        return removed


    # -- authorization codes --
    def put_code(self, code: Code) -> None:
        self._codes[code.code] = code
        if not self.durable:
            return
        try:
            self._db().collection("mcp_oauth_codes").document(code.code).set({
                "client_id": code.client_id, "redirect_uri": code.redirect_uri,
                "code_challenge": code.code_challenge, "email": code.email,
                "subject": code.subject, "resource": code.resource,
                "scope": code.scope, "expires_at": code.expires_at,
            })
        except Exception:
            pass

    def take_code(self, code: str) -> Code | None:
        """Fetch and delete in one step. A code is single-use by construction here, not
        by a later check somebody can forget — replaying one is the classic way an
        intercepted redirect becomes a token."""
        found = self._codes.pop(code, None)
        if self.durable:
            try:
                ref = self._db().collection("mcp_oauth_codes").document(code)
                snap = ref.get()
                ref.delete()
                if found is None and snap.exists:
                    d = snap.to_dict() or {}
                    found = Code(code, d["client_id"], d["redirect_uri"],
                                 d["code_challenge"], d["email"], d["subject"],
                                 d["resource"], d.get("scope", ""), d["expires_at"])
            except Exception:
                pass
        if found and found.expires_at < time.time():
            return None
        return found

    # -- refresh tokens (rotating) --
    def put_refresh(self, token: str, record: dict) -> None:
        key = hashlib.sha256(token.encode()).hexdigest()
        # Stored hashed: the store is the one place every long-lived credential sits
        # together, and a read of it should not be a set of working tokens.
        self._refresh[key] = record
        if not self.durable:
            return
        try:
            self._db().collection("mcp_oauth_refresh").document(key).set(record)
        except Exception:
            pass

    def take_refresh(self, token: str) -> dict | None:
        key = hashlib.sha256(token.encode()).hexdigest()
        found = self._refresh.pop(key, None)
        if self.durable:
            try:
                ref = self._db().collection("mcp_oauth_refresh").document(key)
                snap = ref.get()
                ref.delete()
                if found is None and snap.exists:
                    found = snap.to_dict()
            except Exception:
                pass
        if found and float(found.get("expires_at", 0)) < time.time():
            return None
        return found


# --- tokens ------------------------------------------------------------------
def access_token(key: SigningKey, *, issuer: str, subject: str, email: str,
                 audience: str, client_id: str, scope: str) -> tuple[str, int]:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,      # RFC 8707: this token is for ONE resource, named by the
                              # client at /authorize and re-checked by that resource.
        "email": email,
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + ACCESS_TTL,
        "jti": secrets.token_urlsafe(12),
    }
    return key.sign(claims), ACCESS_TTL
