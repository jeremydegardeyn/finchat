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

import analyst_routing
import sources as backends
import overview as capability

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


@app.get("/v1/customers/by-account/{account_id}/overview",
         response_model=Overview, tags=["customer"])
def customer_overview(account_id: str):
    """One customer view across the transactions and loan domains.

    This function is transport, not logic. The composition and the next-action rule
    live in `overview.py` so a caller without a deployed process service can reuse
    them rather than growing a second copy — which is the whole argument for the layer.
    """
    try:
        return capability.build_overview(account_id, backends, RECENT_LIMIT)
    except backends.NotFound:
        raise HTTPException(404, f"account {account_id} not found") from None
    except backends.SourceUnavailable:
        raise HTTPException(503, "transactions service unavailable") from None


class RouteReq(BaseModel):
    question: str


@app.post("/v1/analyst/route", tags=["analyst"])
def analyst_route(req: RouteReq):
    """Which capability should answer this question.

    Keyword-only here: the model classifiers need credentials the BFF holds (the gateway's
    `on_behalf_of`, the platform token for Vertex), so this endpoint answers with the
    deterministic half rather than pretending to the full decision. Honest about it in the
    response — a caller that reads `classifier` knows whether it got the model's verdict
    or the fallback, which is exactly what nobody could see the session the model path
    failed silently.
    """
    return {"mode": analyst_routing.heuristic_intent(req.question),
            "classifier": "heuristic",
            "modes": list(analyst_routing.MODES)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8090")))
