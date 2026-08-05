"""Refresh-token store for one-time sign-in (ADR-0025).

Why this exists
---------------
Google Identity Services hands out two things and neither produces the other in the
browser: an **ID token** (who you are) and an **access token** (what you may reach). FinChat
needs both — the ID token resolves the persona (ADR-0016), the access token lets BigQuery
evaluate the user's own IAM and policy tags (ADR-0019). Requesting them separately meant
two consent moments, the second landing mid-conversation because the warm-up call is not
in a user-gesture context and the popup gets blocked.

The authorization-code flow returns **both from a single consent**, plus a refresh token.
That refresh token is what turns "once per session" into "once, ever" — and it is the only
reason this module exists, because a refresh token has to live somewhere server-side.

What is stored, and what is not
-------------------------------
Stored: the refresh token, keyed by a SHA-256 of the lowercased email. Nothing else — no
access tokens (short-lived, held in the browser tab's memory), no ID tokens, no profile.

The key is hashed rather than the plain email so the collection's document ids are not
themselves a roster of who uses the platform. That is a small thing, but a document id is
metadata that leaks without ever being read.

Firestore (native mode) is the store: it already exists in this project, it is
scale-to-zero, and it is encrypted at rest by default. When it is unavailable the store
degrades to **no persistence** rather than to an in-memory cache — a refresh token that
silently vanishes on the next cold start would present as "it asked me to log in again
sometimes", which is worse to diagnose than never having worked.

Revocation
----------
`delete()` drops the local copy. It does NOT revoke the grant at Google — only the user or
an admin can do that, from the account's third-party access page. `revoke_at_google()`
does make that call, and sign-out uses it so "sign out" means what it says.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request

GCP_PROJECT = os.getenv("GCP_PROJECT", "")
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE", "ai-gateway")
COLLECTION = os.getenv("USER_TOKEN_COLLECTION", "finchat_user_tokens")


def _key(email: str) -> str:
    """Document id: a hash, so the id list is not a user roster."""
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()


def _client():
    from google.cloud import firestore
    return firestore.Client(project=GCP_PROJECT, database=FIRESTORE_DATABASE)


def available() -> bool:
    if not GCP_PROJECT:
        return False
    try:
        import google.cloud.firestore  # noqa: F401
        return True
    except ImportError:
        return False


def save(email: str, refresh_token: str) -> bool:
    """Persist a refresh token. Returns False when it could not be stored.

    Callers must treat False as "the user will be asked to consent again next session"
    and say so, rather than pretending the login is permanent.
    """
    if not (available() and email and refresh_token):
        return False
    try:
        from datetime import datetime, timezone
        _client().collection(COLLECTION).document(_key(email)).set({
            "refresh_token": refresh_token,
            # Deliberately not the email: the whole point of hashing the key is undone
            # if the document body carries the plaintext anyway.
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return True
    except Exception as e:
        print(f"user_tokens: save failed: {type(e).__name__}: {e}")
        return False


def load(email: str) -> str | None:
    if not (available() and email):
        return None
    try:
        snap = _client().collection(COLLECTION).document(_key(email)).get()
        return snap.get("refresh_token") if snap.exists else None
    except Exception as e:
        print(f"user_tokens: load failed: {type(e).__name__}: {e}")
        return None


def delete(email: str) -> None:
    """Drop the local copy. Does NOT revoke the grant at Google — see revoke_at_google."""
    if not (available() and email):
        return
    try:
        _client().collection(COLLECTION).document(_key(email)).delete()
    except Exception as e:
        print(f"user_tokens: delete failed: {type(e).__name__}: {e}")


def revoke_at_google(refresh_token: str) -> bool:
    """Actually revoke the grant, so 'sign out' is not merely local amnesia."""
    if not refresh_token:
        return False
    try:
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke",
            data=urllib.parse.urlencode({"token": refresh_token}).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


# --- Google OAuth token endpoint ---------------------------------------------

TOKEN_URL = "https://oauth2.googleapis.com/token"


def _post_form(fields: dict) -> dict:
    req = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(fields).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    """Swap an auth code for id_token + access_token + refresh_token.

    `redirect_uri=postmessage` is what the GIS popup code client expects; a real URI here
    fails with redirect_uri_mismatch in a way whose error message does not point at this.
    """
    return _post_form({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "postmessage",
        "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> dict:
    """Mint a new access token. No user interaction — this is the whole point.

    Note the response carries no refresh_token: Google returns one only at the original
    grant, so the stored copy stays authoritative until the user revokes it.
    """
    return _post_form({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    })
