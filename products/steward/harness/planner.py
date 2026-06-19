"""PLANNER — turns the steward's goal into a runtime task list.

Unlike the loan workflow (a fixed Cloud Workflows route), the plan here is *data*:
generated now, and the harness may rewrite it (insert a corrective task) when the
evaluator rejects a step.
"""
from __future__ import annotations

import json

from llm import complete, llm_available
from tools import list_contracts

_PROMPT = """You are the planner for a nightly data-quality / reconciliation steward.
Given the goal and the available data contracts, produce 3-6 concrete ordered tasks.
Return ONLY a JSON array of short task strings.

Goal: {goal}
Contracts: {contracts}
"""


def make_plan(goal: str) -> list[str]:
    contracts = list_contracts()
    if llm_available():
        try:
            raw = complete(_PROMPT.format(goal=goal, contracts=contracts))
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            tasks = json.loads(raw)
            if isinstance(tasks, list) and tasks:
                return [str(t) for t in tasks][:6]
        except Exception:
            pass  # fall through to offline plan

    # Offline deterministic plan. Tasks are kept free of trigger words so only the
    # genuine judgment step trips the evaluator in offline mode.
    return [
        "Gather gold-table partitions for the reconciliation window",
        "Validate records against the active data contracts",
        "Detect and flag reconciliation anomalies",   # this one escalates
        "Summarize findings and propose remediation",
    ]
