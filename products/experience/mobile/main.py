"""FinChat Mobile Experience API — one screen, one round trip (ADR-0030).

The third channel, and the one that makes the layering falsifiable. The web SPA calls
the transactions and loan system APIs separately and assembles the customer view in the
browser; this returns the same screen in a single request, shaped for a phone.

It is an experience API by what it is allowed to do, not by where it sits:

- It **aggregates, reshapes and renames.** `next_action` becomes a headline; amounts are
  pre-formatted; the payload is trimmed to what one screen draws.
- It **decides nothing.** Every business judgement — including which action comes next —
  is the process API's. If this file ever grows a rule about when a customer is in
  trouble, the web and mobile channels can start disagreeing about it, and that is the
  failure mode the layer exists to prevent.
- It **never calls a system API.** `PROCESS_API_URL` is the only backend it knows, and
  `scripts/test_api_layering.py` fails the build if a system API URL appears here.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(
    title="FinChat Mobile Experience API",
    version="1.0.0",
    description="Channel-shaped composition for the mobile home screen.",
)

PROCESS_API_URL = os.getenv("PROCESS_API_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("MOBILE_TIMEOUT", "20"))
ACTIVITY_ON_SCREEN = int(os.getenv("MOBILE_ACTIVITY_ROWS", "3"))


def _id_token(audience: str) -> str | None:
    """An OIDC id-token for a private Cloud Run audience, or None with a reason.

    The metadata identity endpoint, which is the canonical path for Cloud Run
    service-to-service auth. `google.oauth2.id_token.fetch_id_token` reaches the same
    credentials — it pings the metadata server itself — so this is a choice about error
    reporting, not about what works: `fetch_id_token` catches `ImportError` from
    `google.auth.transport.requests` and re-raises it as "Neither metadata server or
    valid service account credentials are found", which sends you looking at IAM when
    the actual fault is that this image never installed `requests`.

    Whatever the cause, it is printed. Returning a bare None here is what made a missing
    dependency look like a broken upstream service for a whole deploy cycle.
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
        print(f"mobile: no id-token for {audience} "
              f"(metadata: {type(metadata_err).__name__}: {metadata_err}; "
              f"fetch_id_token: {type(e).__name__}: {e})")
        return None


def _overview(account_id: str) -> dict:
    if not PROCESS_API_URL:
        raise HTTPException(503, "PROCESS_API_URL is not configured")
    url = f"{PROCESS_API_URL}/v1/customers/by-account/{account_id}/overview"
    headers = {"Accept": "application/json"}
    tok = _id_token(PROCESS_API_URL)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, f"process API: {e.reason}") from None
    except Exception:
        raise HTTPException(503, "process API unavailable") from None


def _money(amount, currency: str) -> str | None:
    """Formatting is a channel concern, so it lives here rather than upstream.

    None is preserved rather than rendered as 0.00 — a masked balance (ADR-0019) must
    reach the screen as "unavailable", not as a number the customer would act on.
    """
    if amount is None:
        return None
    return f"{amount:,.2f} {currency}"


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "process_api": bool(PROCESS_API_URL)}


@app.get("/v1/home", tags=["mobile"])
def home(account_id: str = Query(..., description="Account to render the home screen for")):
    """Everything the mobile home screen draws, in one request.

    The web SPA makes several calls to assemble the same view. That difference is the
    measurable argument for this layer, and it is the number worth quoting rather than
    the architecture diagram.
    """
    o = _overview(account_id)
    currency = o.get("currency", "USD")
    balance = o.get("balance")

    return {
        "headline": o.get("next_action", {}).get("label"),
        "headline_kind": o.get("next_action", {}).get("kind"),
        "balance": {
            "amount": balance,
            "display": _money(balance, currency),
            "masked": balance is None,
            "currency": currency,
        },
        "activity": [
            {
                "id": t.get("transaction_id"),
                "type": t.get("txn_type"),
                "display": _money(t.get("amount"), t.get("currency", currency)),
                "when": t.get("event_time"),
                "posted": t.get("status") == "POSTED",
            }
            for t in (o.get("recent_activity") or [])[:ACTIVITY_ON_SCREEN]
        ],
        "loans": [
            {"id": l.get("loan_id"), "status": l.get("status"),
             "amount_display": _money(l.get("amount"), currency)}
            for l in (o.get("loans") or [])
        ],
        # Named, not hidden. A screen that quietly omits a section the customer expects
        # is worse than one that says which part could not be loaded.
        "unavailable": o.get("partial") or [],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8091")))
