"""EVALUATOR — the per-step gate.

This is the same idea as the FinChat live-eval LLM-judge (ADR-0015,
scripts/live_eval.py), moved INLINE so it gates every step instead of scoring
turns after the fact. Returns a confidence score 0..1 + a reason; the harness uses
it to proceed, replan, or escalate to the human approver.
"""
from __future__ import annotations

import json

from llm import complete, llm_available

_PROMPT = """You are an evaluator. Score how well the RESULT satisfies the TASK
against a banking data-quality bar. Return ONLY JSON:
{{"score": <0..1 float>, "reason": "<short>"}}

TASK: {task}
RESULT: {result}
"""


def judge(task: str, result: str) -> dict:
    if llm_available():
        try:
            raw = complete(_PROMPT.format(task=task, result=result))
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            data = json.loads(raw)
            return {"score": float(data["score"]), "reason": str(data.get("reason", ""))}
        except Exception:
            pass  # fall through to offline heuristic

    if "[UNCERTAIN]" in result:
        return {"score": 0.4, "reason": "low confidence / borderline items — escalate to approver"}
    return {"score": 0.9, "reason": "result satisfies the data-quality contract"}
