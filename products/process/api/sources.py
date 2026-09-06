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
        f"{rel} not found under {[str(r) for r in SEARCH_ROOTS]} — if this is the "
        "container, the Dockerfile is missing a COPY.")

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
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token as gid

        return gid.fetch_id_token(Request(), audience)
    except Exception:
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
