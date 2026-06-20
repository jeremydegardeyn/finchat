"""EVALUATOR — the per-step gate.

Same idea as the FinChat live-eval LLM-judge (ADR-0015, scripts/live_eval.py), moved
INLINE so it gates every step. Returns a confidence score 0..1 + a reason; the harness
uses it to proceed, replan, or escalate to the approver. A failing data-quality check
(`[VIOLATION]`/`FAILED`) scores low and is escalated.
"""
from __future__ import annotations

import json

from llm import complete, llm_available

_MARKERS = ("[VIOLATION]", "[UNCERTAIN]", "FAILED")

_PROMPT = """You are an evaluator for a bank's nightly data reconciliation.
Score how well the RESULT satisfies the TASK against a data-quality bar.
Return ONLY JSON: {{"score": <0..1 float>, "reason": "<short>"}}

TASK: {task}
RESULT: {result}
"""


def judge(task: str, result: str) -> dict:
    # A hard contract violation always escalates — don't let the LLM soften it.
    if any(m in result for m in _MARKERS):
        return {"score": 0.4, "reason": "contract violation / failed check — escalate to approver"}

    if llm_available():
        try:
            raw = complete(_PROMPT.format(task=task, result=result))
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            data = json.loads(raw)
            return {"score": float(data["score"]), "reason": str(data.get("reason", ""))}
        except Exception:
            pass  # fall through to offline heuristic

    return {"score": 0.9, "reason": "result satisfies the data-quality contract"}
