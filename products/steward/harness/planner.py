"""PLANNER — builds the run's work list from the live Dataplex DQ results.

The plan is *derived from reality*: one remediation task per currently-failing DQ
rule (read from Dataplex), not a fixed checklist. No open findings -> empty plan ->
the steward correctly does nothing but report a clean bill of health.
"""
from __future__ import annotations

from tools import read_findings


def plan(goal: str) -> list[dict]:
    # Highest failure rate first (most rows affected -> most urgent).
    findings = read_findings()
    return sorted(findings, key=lambda f: (f.get("passed_count", 0) - (f.get("evaluated", 0))))
