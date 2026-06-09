"""
FinChat Loan API (FastAPI on Cloud Run).

Customer endpoints: submit a loan request, check status.
Employee endpoints: list requests, view audit, record an authenticated decision.

The synchronous submit path runs validate -> credit profile -> overdraft lookup
-> risk score -> recommendation inline (for the demo/UI). The orchestrated,
long-running enterprise path is Cloud Workflows (../workflow/loan_approval.yaml),
which drives the same steps across the 5 ADK agents with a human-in-the-loop wait.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query
from pydantic import BaseModel, Field

from store import LoanStore
from risk import synthesize_credit_profile, score_risk

app = FastAPI(title="FinChat Loan API", version="1.0.0",
              description="Loan submission, status, and authenticated approver decisions.")
store = LoanStore()

TXN_API_URL = os.getenv("TXN_API_URL", "")


# --- models ------------------------------------------------------------------
class LoanSubmission(BaseModel):
    customer_name: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    term_months: int = Field(..., ge=6, le=360)
    account_id: Optional[str] = None


class Decision(BaseModel):
    decision: str = Field(..., pattern="^(APPROVE|REJECT|REQUEST_MODIFICATION|COUNTEROFFER)$")
    rationale: str = ""
    counteroffer_amount: Optional[float] = None


def _overdraft_events(account_id: Optional[str]) -> int:
    """Pull overdraft signal from the Transactions data product (cross-product lineage)."""
    if not account_id or not TXN_API_URL:
        return 0
    try:
        import httpx
        r = httpx.get(f"{TXN_API_URL}/v1/accounts/{account_id}/summary", timeout=6.0)
        if r.status_code == 200:
            s = r.json()
            # withdrawals+fees as a coarse overdraft proxy when negative net balance.
            return s.get("withdrawal_count", 0) if s.get("net_balance", 0) < 0 else 0
    except Exception:
        pass
    return 0


# --- ops ---------------------------------------------------------------------
@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "data_source": store.mode}


# --- customer ----------------------------------------------------------------
@app.post("/v1/loans", tags=["customer"])
def submit_loan(sub: LoanSubmission):
    # 1. validate (pydantic) + 2. create record
    loan = store.create_loan(sub.customer_name, sub.amount, sub.term_months, sub.account_id)
    lid = loan["loan_id"]
    # 3. synthetic credit profile -> 4. store
    profile = synthesize_credit_profile(lid, sub.amount, sub.term_months)
    store.save_profile(lid, {"credit_score": profile.credit_score, "annual_income": profile.annual_income,
                             "existing_debt": profile.existing_debt, "dti_ratio": profile.dti_ratio})
    store.set_status(lid, "PROFILED")
    # 5. overdraft history from transactions product -> 6. risk score + recommendation
    od = _overdraft_events(sub.account_id)
    result = score_risk(profile, sub.amount, overdraft_events=od)
    store.save_risk(lid, result.to_row(), overdraft_events=od)
    # 7. route to human approver
    store.set_status(lid, "PENDING_APPROVAL")
    return {"loan_id": lid, "status": "PENDING_APPROVAL",
            "risk_score": result.risk_score, "recommendation": result.recommendation,
            "reasons": result.reasons,
            # Explainability: structured factor attribution + adverse-action reasons.
            "factors": result.factors, "principal_reasons": result.principal_reasons,
            "model_version": result.model_version}


@app.get("/v1/loans/{loan_id}", tags=["customer"])
def get_loan(loan_id: str):
    loan = store.get_loan(loan_id)
    if not loan:
        raise HTTPException(404, f"loan {loan_id} not found")
    return loan


# --- employee ----------------------------------------------------------------
@app.get("/v1/loans", tags=["employee"])
def list_loans(status: Optional[str] = Query(None)):
    return store.list_loans(status)


@app.get("/v1/loans/{loan_id}/audit", tags=["employee"])
def get_audit(loan_id: str):
    return store.get_audit(loan_id)


class Notify(BaseModel):
    decision: str


@app.post("/v1/loans/{loan_id}/notify", tags=["customer"])
def notify_customer(loan_id: str, body: Notify):
    """Notify the customer of the decision (workflow step 13). Logs + audits."""
    loan = store.get_loan(loan_id)
    if not loan:
        raise HTTPException(404, f"loan {loan_id} not found")
    name = loan.get("customer_name", "Customer")
    message = f"Dear {name}, your loan {loan_id} decision is: {body.decision}."
    store.audit(loan_id, "notification-agent", "NOTIFY_CUSTOMER", message)
    return {"sent": True, "message": message}


@app.post("/v1/loans/{loan_id}/decision", tags=["employee"])
def record_decision(loan_id: str, decision: Decision,
                    x_approver: str = Header(..., description="Authenticated approver identity")):
    """Human-in-the-loop decision. Also the Cloud Workflows callback target.

    Requires an approver identity (simulated via header here; IAM/IAP in prod).
    Every decision is appended immutably and versioned.
    """
    if not store.get_loan(loan_id):
        raise HTTPException(404, f"loan {loan_id} not found")
    if decision.decision == "COUNTEROFFER" and not decision.counteroffer_amount:
        raise HTTPException(400, "counteroffer_amount required for COUNTEROFFER")
    row = store.record_decision(loan_id, decision.decision, approver=x_approver,
                                rationale=decision.rationale,
                                counteroffer_amount=decision.counteroffer_amount)
    return {"loan_id": loan_id, "decision": row["decision"], "version": row["version"],
            "approver": row["approver"], "decided_at": row["decided_at"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8081")))
