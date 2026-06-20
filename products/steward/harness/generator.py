"""GENERATOR — proposes a remediation for a failing DQ rule, and rolls up the run.

Read-only: it produces a remediation *order* (text) for the approver to sign off; it
does not act. Uses Gemini via Vertex when available, else a conservative template.
"""
from __future__ import annotations

from llm import complete, llm_available

_PROMPT = """You are a banking data-steward agent. A Dataplex data-quality rule failed.
Dimension={dimension}  column={column}  scan={scan}
Rows passed: {passed}/{evaluated}
Failing-rows query: {frq}

Propose a concrete, CONSERVATIVE remediation order (what the owning team should do) in
2-3 sentences. Never propose directly mutating production financial tables — prefer
quarantine of bad rows, an upstream fix + backfill, then re-running the scan to verify.
"""


def propose(finding: dict, history: list[dict]) -> str:
    if llm_available():
        try:
            return complete(_PROMPT.format(
                dimension=finding.get("dimension", ""), column=finding.get("column", ""),
                scan=finding.get("scan", ""), passed=finding.get("passed_count", "?"),
                evaluated=finding.get("evaluated", "?"),
                frq=finding.get("failing_rows_query", "(none)")))
        except Exception:
            pass
    ev, pc = finding.get("evaluated", 0), finding.get("passed_count", 0)
    n_bad = max(0, ev - pc)
    return (f"Quarantine the {n_bad} failing row(s) on {finding.get('column') or 'the table'}, "
            f"request an upstream backfill from the owning team, then re-run "
            f"{finding.get('scan')} to verify. (dimension: {finding.get('dimension')})")


def summarize(history: list[dict]) -> str:
    n = len(history)
    applied = sum(1 for h in history if h.get("resolution") == "applied")
    if n == 0:
        return "No open data-quality findings — all Dataplex DQ rules passed; nothing to remediate."
    if llm_available():
        try:
            ctx = "\n".join(f"- {h['task']} -> {h.get('resolution')}" for h in history)
            return complete(
                "Summarize this nightly data-quality remediation run for a banking platform "
                f"in 2-3 sentences (findings, what was approved, what was deferred):\n{ctx}")
        except Exception:
            pass
    return (f"{n} failing rule(s); {applied} remediation order(s) approved, "
            f"{n - applied} deferred/rejected.")
