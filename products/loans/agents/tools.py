"""
Tools for the loan multi-agent system. Each maps to one specialist agent's job.
Reuses the unit-tested risk/credit logic from the loan API package.
"""
from __future__ import annotations

import os
import sys

# Reuse the tested pure logic from the API package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from risk import synthesize_credit_profile, score_risk  # noqa: E402

TXN_API_URL = os.getenv("TXN_API_URL", "")


def generate_credit_profile(loan_id: str, amount: float, term_months: int) -> dict:
    """Generate a synthetic credit profile for a loan (Credit Agent).

    Args:
        loan_id: Loan identifier.
        amount: Requested loan amount.
        term_months: Loan term in months.
    Returns:
        credit_score, annual_income, existing_debt, dti_ratio.
    """
    p = synthesize_credit_profile(loan_id, amount, term_months)
    return {"loan_id": loan_id, "credit_score": p.credit_score, "annual_income": p.annual_income,
            "existing_debt": p.existing_debt, "dti_ratio": p.dti_ratio}


def get_overdraft_history(account_id: str) -> dict:
    """Retrieve overdraft signal from the Transactions data product (Transaction Review Agent).

    Args:
        account_id: The customer's transaction account id.
    Returns:
        overdraft_events and net_balance (0 events if unavailable).
    """
    if not account_id or not TXN_API_URL:
        return {"account_id": account_id, "overdraft_events": 0, "net_balance": None}
    try:
        import httpx
        r = httpx.get(f"{TXN_API_URL}/v1/accounts/{account_id}/summary", timeout=6.0)
        if r.status_code == 200:
            s = r.json()
            events = s.get("withdrawal_count", 0) if s.get("net_balance", 0) < 0 else 0
            return {"account_id": account_id, "overdraft_events": events, "net_balance": s.get("net_balance")}
    except Exception:
        pass
    return {"account_id": account_id, "overdraft_events": 0, "net_balance": None}


def compute_risk(loan_id: str, amount: float, term_months: int, credit_score: int,
                 annual_income: float, existing_debt: float, dti_ratio: float,
                 overdraft_events: int = 0) -> dict:
    """Compute risk score + recommendation from profile + overdraft history (Approval Agent).

    Returns risk_score (0 best..100 worst), recommendation (APPROVE|REVIEW|DECLINE), reasons.
    """
    from risk import CreditProfile
    profile = CreditProfile(loan_id, credit_score, annual_income, existing_debt, dti_ratio)
    result = score_risk(profile, amount, overdraft_events=overdraft_events)
    return {"risk_score": result.risk_score, "recommendation": result.recommendation,
            "reasons": result.reasons, "model_version": result.model_version}


def send_notification(customer_name: str, loan_id: str, decision: str) -> dict:
    """Notify the customer of a loan decision (Notification Agent).

    In the sandbox this logs the message; in prod it integrates email/SMS/push.
    """
    message = f"Dear {customer_name}, your loan {loan_id} decision is: {decision}."
    print(f"[notification] {message}")
    return {"sent": True, "channel": "log", "message": message}
