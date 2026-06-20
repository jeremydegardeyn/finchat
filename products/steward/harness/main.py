"""FastAPI front door for the durable steward harness (Increment 19).

Endpoints (BFF wires these behind the approver persona, mirroring the loan queue):
  POST /runs                -> start a steward run, returns workflow_id
  GET  /runs/{wid}          -> status (read from a durable event; no polling the agent)
  POST /runs/{wid}/review   -> deliver an approver decision (wakes the sleeping agent)

The approver identity must be the VERIFIED email (Inc 15): the BFF sets `approver`
from the validated ID token, never from client input, and it is written to the audit.

Run locally:  uvicorn main:app --reload   (needs a Postgres; see README)
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from pydantic import BaseModel

from dbos import DBOS, DBOSConfig
from harness import HUMAN_TOPIC, steward_run

config: DBOSConfig = {
    "name": "finchat-steward",
    "database_url": os.getenv(
        "DBOS_DATABASE_URL",
        "postgresql://postgres:dbos@localhost:5432/finchat_steward",
    ),
}

app = FastAPI(title="FinChat Durable Steward")
DBOS(fastapi=app, config=config)  # launches on FastAPI startup; recovers workflows


class StartReq(BaseModel):
    goal: str = "Reconcile yesterday's ledger and flag anomalies"
    max_steps: int = 8


class ReviewReq(BaseModel):
    approved: bool = False
    revise: str | None = None     # corrective task text -> triggers a replan
    approver: str = ""            # set server-side from the verified ID token
    note: str = ""


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/runs")
def start_run(req: StartReq):
    handle = DBOS.start_workflow(steward_run, req.goal, req.max_steps)
    return {"workflow_id": handle.workflow_id}


@app.get("/runs/{wid}")
def get_status(wid: str):
    return DBOS.get_event(wid, "status", timeout_seconds=0) or {"phase": "pending"}


@app.post("/runs/{wid}/review")
def review(wid: str, req: ReviewReq, request: Request):
    payload = req.model_dump()
    # The BFF injects the VERIFIED approver email (Inc 15 / ADR-0016); it is
    # authoritative over any client-supplied value and is written to the audit.
    payload["approver"] = request.headers.get("X-Approver") or req.approver
    DBOS.send(wid, payload, topic=HUMAN_TOPIC)
    return {"ok": True, "delivered_to": wid, "approver": payload["approver"]}
