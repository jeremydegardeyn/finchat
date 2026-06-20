"""The durable agent harness (Increment 19 / ADR-0021).

The steward is a **remediation orchestrator on top of Dataplex DQ** — not a DQ engine
(Dataplex Auto DQ already runs the checks, Inc 10). Each run:

  read DQ findings -> [ propose remediation -> assess -> AWAIT approver -> apply ]* -> summarize

Why durable execution (vs scheduled SQL): every remediation is a side effect on
financial data, so each one durably PAUSES for a human approver (hours/days, survives
crashes), then applies EXACTLY-ONCE on approval. That long-running, branching,
human-in-the-loop, exactly-once shape is what a scheduled query cannot do.

  * @DBOS.step  -> checkpointed (read/propose/assess/apply), replayed after a crash
  * DBOS.recv   -> durable wait for the approver (no polling)
  * DBOS.sleep  -> zero-cost nap between items
  * DBOS.set_event -> status projection for the Admin/Approver UI
"""
from __future__ import annotations

import os

from dbos import DBOS

from evaluator import assess
from generator import propose, summarize
from planner import plan
from tools import apply_remediation

STEP_SLEEP = int(os.getenv("STEP_SLEEP_SECONDS", "5"))
HUMAN_WAIT = int(os.getenv("HUMAN_WAIT_SECONDS", str(7 * 24 * 3600)))  # 1 week
HUMAN_TOPIC = "human_review"


@DBOS.step()
def plan_step(goal: str) -> list[dict]:
    return plan(goal)


@DBOS.step()
def propose_step(finding: dict, history: list[dict]) -> str:
    return propose(finding, history)


@DBOS.step()
def assess_step(finding: dict, proposal: str) -> dict:
    return assess(finding, proposal)


@DBOS.step()
def apply_step(finding: dict, proposal: str, decision: dict) -> str:
    return apply_remediation(finding, proposal, decision)


@DBOS.workflow()
def steward_run(goal: str, max_steps: int = 12) -> dict:
    findings = plan_step(goal)
    history: list[dict] = []
    DBOS.set_event("status", {"phase": "planned",
                              "plan": [f["label"] for f in findings], "history": history})

    for i, f in enumerate(findings[:max_steps]):
        proposal = propose_step(f, history)
        verdict = assess_step(f, proposal)
        entry = {"task": f["label"], "result": proposal,
                 "score": verdict["score"], "reason": verdict["reason"]}

        # Remediation = side effect on financial data -> ALWAYS require approval.
        DBOS.set_event("status", {"phase": "awaiting_human", "step": i,
                                  "pending": entry, "history": history})
        decision = DBOS.recv(HUMAN_TOPIC, timeout_seconds=HUMAN_WAIT)

        if decision is None:
            entry["resolution"] = "timeout_auto_defer"
        elif decision.get("approved"):
            entry["applied"] = apply_step(f, proposal, decision)  # exactly-once side effect
            entry["resolution"] = "applied"
            entry["approver"] = decision.get("approver", "")
        else:
            entry["resolution"] = "rejected"
            entry["approver"] = decision.get("approver", "")

        history.append(entry)
        DBOS.set_event("status", {"phase": "working", "step": i, "history": history})
        if i + 1 < len(findings):
            DBOS.sleep(STEP_SLEEP)

    summary = summarize(history)
    DBOS.set_event("status", {"phase": "done", "history": history, "summary": summary})
    return {"goal": goal, "findings": history, "summary": summary, "done": True}
