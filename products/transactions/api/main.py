"""
FinChat Transactions DaaS API (FastAPI on Cloud Run).

Data-as-a-Service over the BigQuery Gold serving layer. Contract-first: this app
generates OpenAPI 3 at /openapi.json; the API Gateway uses openapi.gateway.yaml
(Swagger 2.0), which imports 1:1 into Apigee for the enterprise path (ADR-0006).
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from bq import Repository

app = FastAPI(
    title="FinChat Transactions DaaS",
    version="1.0.0",
    description="Data-as-a-Service API over the Gold serving layer for retail banking.",
)
repo = Repository()


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
    row = repo.get_balance(account_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"account {account_id} not found")
    return row


@app.get("/v1/accounts/{account_id}/transactions", response_model=list[Transaction], tags=["accounts"])
def get_transaction_history(account_id: str, limit: int = Query(50, ge=1, le=500)):
    rows = repo.get_transactions(account_id, limit)
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
