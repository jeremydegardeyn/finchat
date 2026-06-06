"""
Loan risk scoring + synthetic credit profile generation (pure, Beam/ADK-free).

Deterministic and unit-testable. Used by the Approval agent and the loan API.
Risk score is 0 (best) .. 100 (worst); recommendation thresholds are explicit
and auditable (no black box) — appropriate for a regulated credit decision.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict

MODEL_VERSION = "risk-1.0.0"

# Recommendation thresholds (documented & auditable).
APPROVE_MAX = 35   # score <= 35 -> APPROVE
DECLINE_MIN = 65   # score >= 65 -> DECLINE; between -> REVIEW


def _seed(loan_id: str) -> int:
    return int(hashlib.sha256(loan_id.encode()).hexdigest(), 16)


@dataclass
class CreditProfile:
    loan_id: str
    credit_score: int
    annual_income: float
    existing_debt: float
    dti_ratio: float


def synthesize_credit_profile(loan_id: str, amount: float, term_months: int) -> CreditProfile:
    """Generate a deterministic synthetic credit profile from the loan id.

    Deterministic so the same loan always yields the same profile (reproducible
    audits). In production this is replaced by a bureau pull / Credit Agent.
    """
    import random
    rng = random.Random(_seed(loan_id))
    credit_score = rng.randint(540, 820)
    annual_income = round(rng.uniform(35_000, 180_000), 2)
    existing_debt = round(rng.uniform(0, annual_income * 0.6), 2)
    monthly_income = annual_income / 12.0
    monthly_new_debt = amount / max(term_months, 1)
    dti_ratio = round((existing_debt / 12.0 + monthly_new_debt) / max(monthly_income, 1), 3)
    return CreditProfile(loan_id, credit_score, annual_income, existing_debt, dti_ratio)


@dataclass
class RiskResult:
    risk_score: int
    recommendation: str
    reasons: list[str]
    model_version: str = MODEL_VERSION

    def to_row(self) -> dict:
        d = asdict(self)
        d["reasons"] = json.dumps(self.reasons)
        return d


def score_risk(profile: CreditProfile, amount: float, overdraft_events: int = 0,
               overdraft_ratio: float = 0.0) -> RiskResult:
    """Combine credit score, DTI, overdraft history, and loan size into a risk score."""
    reasons: list[str] = []
    score = 0

    # Credit score component (0-40).
    if profile.credit_score >= 760:
        score += 0; reasons.append(f"excellent credit ({profile.credit_score})")
    elif profile.credit_score >= 700:
        score += 10; reasons.append(f"good credit ({profile.credit_score})")
    elif profile.credit_score >= 640:
        score += 22; reasons.append(f"fair credit ({profile.credit_score})")
    else:
        score += 40; reasons.append(f"poor credit ({profile.credit_score})")

    # DTI component (0-30).
    if profile.dti_ratio <= 0.28:
        score += 0; reasons.append(f"healthy DTI ({profile.dti_ratio})")
    elif profile.dti_ratio <= 0.43:
        score += 15; reasons.append(f"elevated DTI ({profile.dti_ratio})")
    else:
        score += 30; reasons.append(f"high DTI ({profile.dti_ratio})")

    # Overdraft history component (0-20) — sourced from the Transactions product.
    if overdraft_events == 0:
        reasons.append("no overdraft history")
    elif overdraft_events <= 2:
        score += 10; reasons.append(f"{overdraft_events} overdraft event(s)")
    else:
        score += 20; reasons.append(f"frequent overdrafts ({overdraft_events})")

    # Loan size component (0-10).
    if amount > 50_000:
        score += 10; reasons.append("large loan amount")
    elif amount > 20_000:
        score += 5; reasons.append("moderate loan amount")

    score = max(0, min(100, score))
    if score <= APPROVE_MAX:
        rec = "APPROVE"
    elif score >= DECLINE_MIN:
        rec = "DECLINE"
    else:
        rec = "REVIEW"
    return RiskResult(risk_score=score, recommendation=rec, reasons=reasons)
