"""Offline unit tests for the steward's remediation logic.

Run with NO GCP_PROJECT and NO Dataplex client — exercises planning/proposing/
assessing/summarizing against the offline stub finding. The real Dataplex reads and
scan re-runs are exercised by the live deploy, not unit tests.
Run: cd products/steward/harness && python -m pytest -q
"""
from planner import plan
from generator import propose, summarize
from evaluator import assess
from tools import apply_remediation


def test_plan_returns_findings():
    findings = plan("Remediate open data-quality findings")
    assert isinstance(findings, list) and findings
    f = findings[0]
    assert {"id", "scan", "label", "dimension"} <= set(f)


def test_propose_is_conservative_text():
    f = plan("x")[0]
    p = propose(f, [])
    assert isinstance(p, str) and len(p) > 10
    assert "production" not in p.lower() or "never" in p.lower()  # no direct-mutation proposal


def test_assess_shape_and_range():
    f = plan("x")[0]
    v = assess(f, "quarantine + backfill")
    assert set(v) == {"score", "reason"} and 0.0 <= v["score"] <= 1.0


def test_apply_records_order_offline():
    f = plan("x")[0]
    out = apply_remediation(f, "quarantine + backfill", {"approved": True,
                                                         "approver": "jeremy@datadinosaur.com"})
    assert "approved by jeremy@datadinosaur.com" in out


def test_summary_clean_vs_findings():
    assert "nothing to remediate" in summarize([]).lower()
    assert "applied" in summarize([{"task": "t", "resolution": "applied"}]).lower() \
        or "remediation" in summarize([{"task": "t", "resolution": "applied"}]).lower()
