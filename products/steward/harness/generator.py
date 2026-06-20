"""GENERATOR — executes ONE task (the 'act' step).

For a reconciliation task it runs the real BigQuery data-quality check for that
product's table; for the summary task it rolls up the findings (Gemini-on-Vertex if
available, else a deterministic rollup). Stateless: everything arrives as arguments,
so the durable engine can replay it after a crash.
"""
from __future__ import annotations

import re

from llm import complete, llm_available
from tools import checks, run_dq_check

_TARGET = re.compile(r"\(([^.()]+)\.([^.()]+)\)")


def run_step(task: str, history: list[dict]) -> str:
    m = _TARGET.search(task)
    if m:
        ds, tbl = m.group(1), m.group(2)
        expect_fresh = next((f for _pid, d, t, f in checks() if d == ds and t == tbl), False)
        find = run_dq_check(ds, tbl, expect_fresh)
        # [VIOLATION] marks a failing check so the evaluator scores it low -> escalate.
        prefix = "OK" if find["ok"] else "[VIOLATION]"
        return f"{prefix} {find['target']}: {find['detail']}"

    if "summar" in task.lower():
        violations = [h for h in history if "[VIOLATION]" in (h.get("result") or "")]
        if llm_available():
            try:
                ctx = "\n".join(f"- {h['task']}: {h['result']}" for h in history) or "(none)"
                return complete(
                    "Summarize this nightly data reconciliation for a banking platform in "
                    "2-3 sentences. Call out any contract violations and recommend next steps.\n"
                    f"{ctx}")
            except Exception:
                pass
        if violations:
            return (f"{len(violations)} contract violation(s): "
                    + "; ".join(h["result"] for h in violations))
        return "All data products passed their contract checks."

    return f"Noted: {task}"
