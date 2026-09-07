"""System-API access for the process layer (ADR-0030).

Named `sources`, not `backends`, because `mcp_server/backends.py` already exists: two
modules with one name resolve to whichever imported first in a whole-repo pytest run,
and CI's per-directory working directories hide it completely. That is the third time
that collision has bitten in this codebase.

The process layer reaches data only through the system APIs that own it. It holds no
schema, no dataset name and no SQL — if this file ever imports `google.cloud.bigquery`,
the layering has collapsed and the middle tier has become a second copy of the domain.

Same two-transport shape as `mcp_server/backends.py`: HTTP against the private Cloud Run
services when their URLs are configured, and an in-process demo fallback reusing the
APIs' own sample repositories otherwise, so the service runs and is testable with no GCP
access at all.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent

# The demo repositories sit at the same relative path under two different roots: the repo
# checkout, and the image, where the Dockerfile copies them next to this file. Walking a
# fixed number of parents is correct in exactly one of those — in the image it climbs out
# of /app entirely and fails at the first demo call, long after the build went green.
SEARCH_ROOTS = (HERE, REPO_ROOT)


def _resolve(*parts: str) -> Path:
    rel = Path(*parts)
    for root in SEARCH_ROOTS:
        if (root / rel).is_file():
            return root / rel
    raise FileNotFoundError(
        f"{rel} not found under {[str(r) for r in SEARCH_ROOTS]}. The demo repositories "
        "are a checkout-only convenience and are not shipped in the image — set "
        "TXN_API_URL and LOAN_API_URL, which a deployed service always has.")

TXN_API_URL = os.getenv("TXN_API_URL", "").rstrip("/")
LOAN_API_URL = os.getenv("LOAN_API_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("PROCESS_TIMEOUT", "20"))


class SourceUnavailable(RuntimeError):
    """A system API could not be reached. The caller degrades; it does not guess."""


class NotFound(LookupError):
    """The system API answered, and the thing is not there."""


def mode() -> dict:
    return {"transactions": "http" if TXN_API_URL else "demo",
            "loans": "http" if LOAN_API_URL else "demo"}


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _id_token(audience: str) -> str | None:
    """Mint an OIDC id-token for a private Cloud Run audience.

    The metadata identity endpoint, which is the canonical path for Cloud Run
    service-to-service auth and the one `ui/server.py` already uses.

    `google.oauth2.id_token.fetch_id_token` would also work here — contrary to what an
    earlier version of this comment claimed, it pings the metadata server and falls back
    to exactly these credentials on Cloud Run. It is not used because it reports failure
    as `DefaultCredentialsError: Neither metadata server or valid service account
    credentials are found`, and the fault it most often hides is neither of those: it
    catches `ImportError` from `google.auth.transport.requests`, which needs the
    `requests` package that `google-auth` does not install. That is the bug that shipped
    — a missing dependency, reported as missing credentials, surfacing to the caller as
    "the other service is unavailable". Constructing the credentials directly lets the
    ImportError out where it says what it is, and `scripts/test_service_requirements.py`
    stops it recurring.

    Either way the failure is logged rather than swallowed. A silent None here is
    indistinguishable from a service being down, and that is what actually cost a deploy
    cycle: the first fix changed which call minted the token, which was never the fault.
    """
    try:
        from google.auth import compute_engine
        from google.auth.transport.requests import Request as GReq

        creds = compute_engine.IDTokenCredentials(
            GReq(), target_audience=audience, use_metadata_identity_endpoint=True)
        creds.refresh(GReq())
        return creds.token
    except Exception as e:
        metadata_err = e

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token as gid

        return gid.fetch_id_token(Request(), audience)
    except Exception as e:
        print(f"process: no id-token for {audience} "
              f"(metadata: {type(metadata_err).__name__}: {metadata_err}; "
              f"fetch_id_token: {type(e).__name__}: {e})")
        return None


def _get(base: str, path: str, params: dict | None = None):
    url = f"{base}{path}"
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    if clean:
        url = f"{url}?{urllib.parse.urlencode(clean)}"
    headers = {"Accept": "application/json"}
    tok = _id_token(base)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(path) from None
        raise SourceUnavailable(f"{path} -> {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SourceUnavailable(f"{path} -> {type(e).__name__}") from None


# --- demo repositories, reused from the services that own them ---------------
def _txn_repo():
    os.environ.setdefault("DEMO_MODE", "1")
    return _load("process_txn_repo",
                 _resolve("products", "transactions", "api", "bq.py")).Repository()


def _loan_store():
    os.environ.setdefault("DEMO_MODE", "1")
    return _load("process_loan_store",
                 _resolve("products", "loans", "api", "store.py")).LoanStore()


# --- capabilities ------------------------------------------------------------
def balance(account_id: str) -> dict:
    if TXN_API_URL:
        return _get(TXN_API_URL, f"/v1/accounts/{account_id}/balance")
    row = _txn_repo().get_balance(account_id)
    if not row:
        raise NotFound(account_id)
    return row


def recent_transactions(account_id: str, limit: int) -> list[dict]:
    if TXN_API_URL:
        try:
            return _get(TXN_API_URL, f"/v1/accounts/{account_id}/transactions",
                        {"limit": limit})
        except NotFound:
            return []  # an account with no transactions is not an error here
    return _txn_repo().get_transactions(account_id, limit) or []


def sample_account() -> str | None:
    """An account id with activity, for the deep health check. None if unavailable.

    Exists so `/healthz/deep` can exercise the real composition path without a caller
    supplying an account. It reaches for the same system API a channel would, which is
    the point — a health check that skips the hops it is meant to prove is decoration.
    """
    if TXN_API_URL:
        try:
            ids = (_get(TXN_API_URL, "/v1/accounts/samples", {"n": 1}) or {}).get(
                "account_ids") or []
            return ids[0] if ids else None
        except (SourceUnavailable, NotFound):
            return None
    ids = _txn_repo().get_sample_accounts(1) or []
    return ids[0] if ids else None


def loans_for_account(account_id: str) -> list[dict]:
    """Loans for one account, selected by the loan API rather than filtered here.

    `account_id` was added to `GET /v1/loans` for this call. Fetching the queue and
    filtering in Python would work and would be the wrong layer: selection belongs to
    the system that owns the rows, and doing it here means moving 200 records over the
    wire to keep two.
    """
    if LOAN_API_URL:
        return _get(LOAN_API_URL, "/v1/loans", {"account_id": account_id}) or []
    return _loan_store().list_loans(None, account_id=account_id) or []
