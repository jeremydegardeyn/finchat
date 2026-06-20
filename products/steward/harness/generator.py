"""GENERATOR — executes ONE task (the 'act' step). May call tools.

Stateless: everything it needs arrives as arguments, so the durable engine can
replay it after a crash without side-effect drift.
"""
from __future__ import annotations

from llm import complete, llm_available
from tools import run_dq_check

_PROMPT = """You are executing one step of a data-quality steward's plan.
Prior step results:
{history}

Do this task and report the result in 1-3 sentences:
{task}
"""


def run_step(task: str, history: list[dict]) -> str:
    if llm_available():
        try:
            ctx = "\n".join(f"- {h['task']}: {h['result']}" for h in history) or "(none)"
            return complete(_PROMPT.format(history=ctx, task=task))
        except Exception:
            pass  # fall through to offline behavior

    t = task.lower()
    # Tasks needing genuine judgment emit an [UNCERTAIN] marker -> the evaluator
    # scores them low -> the harness escalates to the human approver.
    if any(k in t for k in ("anomal", "flag", "remediat", "approv")):
        return (f"[UNCERTAIN] Worked '{task}'. {run_dq_check('reconciliation')} "
                f"2 items are borderline and need human judgment.")
    return f"Completed '{task}'. {run_dq_check('reconciliation')}"
