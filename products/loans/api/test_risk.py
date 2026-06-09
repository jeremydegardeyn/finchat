"""Unit tests for loan risk scoring + synthetic credit profile (no GCP)."""
from risk import synthesize_credit_profile, score_risk, APPROVE_MAX, DECLINE_MIN, CreditProfile


def test_profile_is_deterministic():
    a = synthesize_credit_profile("loan-xyz", 10000, 36)
    b = synthesize_credit_profile("loan-xyz", 10000, 36)
    assert a == b
    assert 540 <= a.credit_score <= 820


def test_strong_applicant_approves():
    p = CreditProfile("l1", credit_score=780, annual_income=150000, existing_debt=10000, dti_ratio=0.15)
    r = score_risk(p, amount=10000, overdraft_events=0)
    assert r.recommendation == "APPROVE"
    assert r.risk_score <= APPROVE_MAX


def test_weak_applicant_declines():
    p = CreditProfile("l2", credit_score=580, annual_income=30000, existing_debt=25000, dti_ratio=0.55)
    r = score_risk(p, amount=60000, overdraft_events=5)
    assert r.recommendation == "DECLINE"
    assert r.risk_score >= DECLINE_MIN


def test_borderline_reviews():
    p = CreditProfile("l3", credit_score=660, annual_income=60000, existing_debt=20000, dti_ratio=0.35)
    r = score_risk(p, amount=25000, overdraft_events=1)
    assert r.recommendation == "REVIEW"


def test_overdrafts_increase_risk():
    p = CreditProfile("l4", credit_score=720, annual_income=80000, existing_debt=10000, dti_ratio=0.2)
    low = score_risk(p, 10000, overdraft_events=0)
    high = score_risk(p, 10000, overdraft_events=5)
    assert high.risk_score > low.risk_score


def test_reasons_present_and_serializable():
    p = synthesize_credit_profile("l5", 15000, 48)
    r = score_risk(p, 15000, 0)
    row = r.to_row()
    assert isinstance(row["reasons"], str) and row["model_version"]
    assert isinstance(row["factors"], str)  # JSON-encoded structured attributions


def test_factors_explain_the_score():
    # Explainability: structured factor points sum exactly to the risk score.
    p = CreditProfile("l6", credit_score=600, annual_income=40000, existing_debt=22000, dti_ratio=0.5)
    r = score_risk(p, amount=55000, overdraft_events=4)
    assert sum(f["points"] for f in r.factors) == r.risk_score
    assert {f["code"] for f in r.factors} == {"CREDIT", "DTI", "OVERDRAFT", "LOAN_SIZE"}
    # Every factor carries its weight + a human-readable note.
    assert all(f["note"] and f["max_points"] > 0 for f in r.factors)
    # Adverse-action principal reasons are ranked, highest-risk first.
    pr = r.principal_reasons
    assert pr and pr[0].startswith(("CREDIT", "DTI"))  # the heaviest factors


def test_strong_applicant_has_no_principal_reasons():
    p = CreditProfile("l7", credit_score=800, annual_income=200000, existing_debt=5000, dti_ratio=0.1)
    r = score_risk(p, amount=8000, overdraft_events=0)
    assert r.principal_reasons == []  # nothing increased risk
