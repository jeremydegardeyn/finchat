"""FinChat Process API — business capabilities that span systems (ADR-0030).

The middle layer of API-led connectivity. A **system** API is shaped by the domain it
owns; an **experience** API is shaped by one channel. This layer is shaped by a business
capability, and it exists because the alternative is every channel re-deriving the same
answer slightly differently.

`GET /v1/customers/by-account/{account_id}/overview` composes the transactions and loan
domains into one customer view, and decides the **next action** — the single most useful
thing this customer could do now. That decision is the reason this layer is not optional:
it is a business rule, it will change, and if the web and mobile channels each own a copy
they will eventually disagree about whether a customer is in trouble.

What this layer must never become is a second domain model. It composes, reshapes and
decides; it does not store, and it reaches its data only through the system APIs that
own it.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import backends

app = FastAPI(
    title="FinChat Process API",
    version="1.0.0",
    description="Cross-domain business capabilities: customer overview and next action.",
)

RECENT_LIMIT = int(os.getenv("PROCESS_RECENT_LIMIT", "5"))


class NextAction(BaseModel):
    kind: str
    label: str
    reason: str


class Overview(BaseModel):
    account_id: str
    currency: str
    balance: Optional[float] = None
    last_activity_at: Optional[str] = None
    recent_activity: list[dict] = []
    loans: list[dict] = []
    next_action: NextAction
    partial: list[str] = []


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "sources": backends.mode()}


def decide_next_action(balance: Optional[float], loans: list[dict],
                       recent: list[dict]) -> NextAction:
    """The one business rule in this service, and the reason the layer exists.

    Ordered by what a customer most needs to know, not by what is easiest to compute.
    A pending loan decision outranks an overdraft because the customer can act on
    neither, but only one of them is news.

    A masked balance is deliberately NOT treated as zero. Column-level security returns
    NULL to a reader without fine-grained access (ADR-0019), and reading that as "no
    money" would turn a policy outcome into a false alarm — the `masked_null` refusal
    rule, applied to a decision rather than to prose.
    """
    pending = [l for l in loans if l.get("status") == "PENDING_APPROVAL"]
    if pending:
        return NextAction(kind="await_loan_decision",
                          label="Your loan application is with an approver",
                          reason=f"loan {pending[0].get('loan_id')} is PENDING_APPROVAL")

    decided = [l for l in loans if l.get("status") in ("APPROVED", "REJECTED")]
    if decided:
        latest = decided[0]
        return NextAction(kind="review_loan_decision",
                          label=f"Your loan was {latest.get('status', '').lower()}",
                          reason=f"loan {latest.get('loan_id')} has a final decision")

    if balance is None:
        return NextAction(kind="none", label="Nothing needs your attention",
                          reason="balance is masked at this access level, so no "
                                 "balance-based advice is offered")

    if balance < 0:
        return NextAction(kind="cover_overdraft",
                          label="Your balance is negative",
                          reason=f"balance {balance} is below zero")

    if not recent:
        return NextAction(kind="none", label="Nothing needs your attention",
                          reason="no posted activity in the recent window")

    return NextAction(kind="none", label="Nothing needs your attention",
                      reason="no pending decision and the balance is positive")


@app.get("/v1/customers/by-account/{account_id}/overview",
         response_model=Overview, tags=["customer"])
def customer_overview(account_id: str):
    """One customer view across the transactions and loan domains.

    Degrades per source rather than failing whole. A loan service that is down should
    not cost the customer their balance, so an unreachable source is named in `partial`
    and the view is returned without it — the channel can then say what is missing
    instead of showing a spinner or, worse, a confident but incomplete page.
    """
    partial: list[str] = []

    try:
        bal = backends.balance(account_id)
    except backends.NotFound:
        raise HTTPException(404, f"account {account_id} not found") from None
    except backends.SourceUnavailable:
        raise HTTPException(503, "transactions service unavailable") from None

    try:
        recent = backends.recent_transactions(account_id, RECENT_LIMIT)
    except backends.SourceUnavailable:
        recent, _ = [], partial.append("recent_activity")

    try:
        loans = backends.loans_for_account(account_id)
    except backends.SourceUnavailable:
        loans, _ = [], partial.append("loans")

    return Overview(
        account_id=account_id,
        currency=bal.get("currency", "USD"),
        balance=bal.get("balance"),
        last_activity_at=bal.get("last_activity_at"),
        recent_activity=recent,
        loans=loans,
        next_action=decide_next_action(bal.get("balance"), loans, recent),
        partial=partial,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8090")))
