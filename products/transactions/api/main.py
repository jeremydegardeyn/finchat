"""
FinChat Transactions DaaS API (FastAPI on Cloud Run).

Data-as-a-Service over the BigQuery Gold serving layer. Contract-first: this app
generates OpenAPI 3 at /openapi.json; the API Gateway uses openapi.gateway.yaml
(Swagger 2.0), which imports 1:1 into Apigee for the enterprise path (ADR-0006).
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

import bt
from bq import Repository

# Distributed tracing (ADR-0035). Same seven-line bootstrap as every other service; see
# ui/server.py for why each service has to carry it.
try:
    import tracing                                        # image: copied beside us
except ImportError:                                       # checkout: walk up to the root
    import sys as _sys
    _d = os.path.dirname(os.path.abspath(__file__))
    while _d != os.path.dirname(_d):
        if os.path.isdir(os.path.join(_d, "observability")):
            _sys.path.insert(0, os.path.join(_d, "observability")); break
        _d = os.path.dirname(_d)
    import tracing

tracing.init("txn-api")

app = FastAPI(
    title="FinChat Transactions DaaS",
    version="1.0.0",
    description="Data-as-a-Service API over the Gold serving layer for retail banking.",
)
repo = Repository()


@app.middleware("http")
async def _trace_requests(request: Request, call_next):
    """Continue the caller's trace. The route TEMPLATE is the span name — the resolved
    path carries an account id, and an account id is an identifier, not a label."""
    if not tracing.ENABLED:
        return await call_next(request)
    route = request.scope.get("route")
    name = getattr(route, "path", None) or request.url.path
    with tracing.server_span(f"{request.method} {name}", request.headers, route=name):
        resp = await call_next(request)
        tracing.set_attrs(http_status=resp.status_code)
        return resp


# --- response models ---------------------------------------------------------
class Balance(BaseModel):
    account_id: str
    currency: str
    balance: float
    last_activity_at: Optional[str] = None


class Transaction(BaseModel):
    transaction_id: str
    txn_type: str
    amount: float
    currency: str
    status: str
    event_time: str


class AccountSummary(BaseModel):
    account_id: str
    customer_id: Optional[str] = None
    account_type: Optional[str] = None
    currency: str
    status: Optional[str] = None
    deposit_count: int
    withdrawal_count: int
    fee_count: int
    net_balance: float
    last_activity_at: Optional[str] = None


# --- ops endpoints -----------------------------------------------------------
@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "data_source": repo.mode}


# --- DaaS endpoints ----------------------------------------------------------
@app.get("/v1/accounts/samples", tags=["accounts"])
def get_sample_accounts(n: int = Query(5, ge=1, le=20)):
    """Real account ids with activity (UI prefill; not a customer-facing endpoint)."""
    return {"account_ids": repo.get_sample_accounts(n)}


@app.get("/v1/accounts/{account_id}/balance", response_model=Balance, tags=["accounts"])
def get_balance(account_id: str):
    # Hot path (ADR-0017): Bigtable point read when BIGTABLE_INSTANCE is set;
    # BigQuery (the analytical source of truth) remains the fallback.
    row = None
    if bt.enabled():
        with tracing.span("bigtable.point_read", client=True):
            row = bt.get_balance(account_id)
            tracing.set_attrs(cache="hit" if row else "miss")
    if not row:
        with tracing.span("bq.get_balance", client=True, cache="bigquery"):
            row = repo.get_balance(account_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found")
    return row


@app.get("/v1/accounts/{account_id}/transactions", response_model=list[Transaction], tags=["accounts"])
def get_transaction_history(account_id: str, limit: int = Query(50, ge=1, le=500)):
    # Hot path: newest-first prefix scan on account_id#reverse_ts (ADR-0017).
    rows = []
    if bt.enabled():
        with tracing.span("bigtable.prefix_scan", client=True):
            rows = bt.get_transactions(account_id, limit)
            tracing.set_attrs(rows=len(rows), cache="hit" if rows else "miss")
    if not rows:
        with tracing.span("bq.get_transactions", client=True, cache="bigquery"):
            rows = repo.get_transactions(account_id, limit)
            tracing.set_attrs(rows=len(rows))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no transactions for account {account_id}")
    return rows


@app.get("/v1/accounts/{account_id}/activity", response_model=list[Transaction], tags=["accounts"])
def get_recent_activity(account_id: str, days: int = Query(30, ge=1, le=365)):
    return repo.get_recent_activity(account_id, days)


@app.get("/v1/accounts/{account_id}/summary", response_model=AccountSummary, tags=["accounts"])
def get_account_summary(account_id: str):
    row = repo.get_summary(account_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found")
    return row


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
