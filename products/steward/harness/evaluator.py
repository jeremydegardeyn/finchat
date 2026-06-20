"""EVALUATOR — risk/confidence assessment of a proposed remediation.

Remediation is a side effect on financial data, so the harness ALWAYS routes it to a
human; this assessment is the recommendation shown to the approver (it does not
auto-apply). Higher failure rate -> lower confidence -> a stronger "review carefully".
"""
from __future__ import annotations


def assess(finding: dict, proposal: str) -> dict:
    ev = finding.get("evaluated", 0) or 0
    pc = finding.get("passed_count", 0) or 0
    fail_ratio = 0.0 if ev == 0 else (ev - pc) / ev
    score = round(max(0.0, 1.0 - fail_ratio * 2), 2)  # many failures -> low confidence
    if fail_ratio == 0:
        reason = "rule failed but no rows quantified — review the scan"
    elif fail_ratio < 0.05:
        reason = f"small blast radius ({fail_ratio:.1%} rows) — low-risk remediation"
    else:
        reason = f"large blast radius ({fail_ratio:.1%} rows) — review carefully before approving"
    return {"score": score, "reason": reason}
