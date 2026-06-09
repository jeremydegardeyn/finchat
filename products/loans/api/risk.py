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


def _factor(code: str, label: str, value, points: int, max_points: int, note: str) -> dict:
    """One explainable scorecard factor. `points` is the risk contributed
    (0 = favorable, up to max_points = worst); higher total = higher risk."""
    return {"code": code, "label": label, "value": str(value),
            "points": points, "max_points": max_points, "note": note,
            "impact": "increases risk" if points > 0 else "favorable"}


@dataclass
class RiskResult:
    risk_score: int
    recommendation: str
    reasons: list[str]
    factors: list[dict]                 # structured per-factor attribution (explainability)
    model_version: str = MODEL_VERSION

    def to_row(self) -> dict:
        d = asdict(self)
        d["reasons"] = json.dumps(self.reasons)
        d["factors"] = json.dumps(self.factors)
        return d

    @property
    def principal_reasons(self) -> list[str]:
        """Adverse-action style: the factors driving risk most, highest first
        (ECOA/Reg B 'principal reasons'). Empty when nothing increased risk."""
        ranked = sorted([f for f in self.factors if f["points"] > 0],
                        key=lambda f: -f["points"])
        return [f"{f['code']}: {f['note']}" for f in ranked[:4]]


def score_risk(profile: CreditProfile, amount: float, overdraft_events: int = 0,
               overdraft_ratio: float = 0.0) -> RiskResult:
    """Transparent additive scorecard over four factors. Each factor's point
    contribution is captured so the decision is fully explainable (reason codes
    for adverse-action notices; SR 11-7 model transparency)."""
    factors: list[dict] = []

    # Credit score factor (0-40).
    cs = profile.credit_score
    if cs >= 760:
        factors.append(_factor("CREDIT", "Credit score", cs, 0, 40, f"excellent credit ({cs})"))
    elif cs >= 700:
        factors.append(_factor("CREDIT", "Credit score", cs, 10, 40, f"good credit ({cs})"))
    elif cs >= 640:
        factors.append(_factor("CREDIT", "Credit score", cs, 22, 40, f"fair credit ({cs})"))
    else:
        factors.append(_factor("CREDIT", "Credit score", cs, 40, 40, f"poor credit ({cs})"))

    # Debt-to-income factor (0-30).
    dti = profile.dti_ratio
    if dti <= 0.28:
        factors.append(_factor("DTI", "Debt-to-income", dti, 0, 30, f"healthy DTI ({dti})"))
    elif dti <= 0.43:
        factors.append(_factor("DTI", "Debt-to-income", dti, 15, 30, f"elevated DTI ({dti})"))
    else:
        factors.append(_factor("DTI", "Debt-to-income", dti, 30, 30, f"high DTI ({dti})"))

    # Overdraft history factor (0-20) — sourced from the Transactions product.
    if overdraft_events == 0:
        factors.append(_factor("OVERDRAFT", "Overdraft history", 0, 0, 20, "no overdraft history"))
    elif overdraft_events <= 2:
        factors.append(_factor("OVERDRAFT", "Overdraft history", overdraft_events, 10, 20,
                               f"{overdraft_events} overdraft event(s)"))
    else:
        factors.append(_factor("OVERDRAFT", "Overdraft history", overdraft_events, 20, 20,
                               f"frequent overdrafts ({overdraft_events})"))

    # Loan size factor (0-10).
    if amount > 50_000:
        factors.append(_factor("LOAN_SIZE", "Loan amount", amount, 10, 10, "large loan amount"))
    elif amount > 20_000:
        factors.append(_factor("LOAN_SIZE", "Loan amount", amount, 5, 10, "moderate loan amount"))
    else:
        factors.append(_factor("LOAN_SIZE", "Loan amount", amount, 0, 10, "modest loan amount"))

    score = max(0, min(100, sum(f["points"] for f in factors)))
    reasons = [f["note"] for f in factors]  # prose, kept for backward compatibility
    if score <= APPROVE_MAX:
        rec = "APPROVE"
    elif score >= DECLINE_MIN:
        rec = "DECLINE"
    else:
        rec = "REVIEW"
    return RiskResult(risk_score=score, recommendation=rec, reasons=reasons, factors=factors)
