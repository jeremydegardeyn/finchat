"""PLANNER — turns the steward's goal into a runtime task list.

For reconciliation the plan is derived deterministically from the data products to
check (more auditable than asking an LLM to invent the route): one task per product
+ a summary. The harness may still rewrite it (insert a corrective task) when the
evaluator rejects a step.
"""
from __future__ import annotations

from tools import checks


def make_plan(goal: str) -> list[str]:
    # Embed the BigQuery target as "(dataset.table)" so the generator can run the
    # real check for each product; keep the text human-readable for the UI/audit.
    tasks = [f"Reconcile {pid} ({ds}.{tbl})" for pid, ds, tbl, _ in checks()]
    tasks.append("Summarize reconciliation findings")
    return tasks
