"""The durable agent harness (Increment 19 / ADR-0021).

    PLAN -> [ GENERATE -> EVALUATE -> (proceed | escalate-to-approver) -> SLEEP ]* -> DONE

Contrast with the loan workflow (Inc 4): there, long-running state lives in the
Cloud Workflows execution and the route is fixed YAML. Here the AGENT owns a durable
loop and the plan is data:

  * Each @DBOS.step is checkpointed to Postgres. Crash / scale-to-zero mid-run ->
    the workflow RESUMES from the last completed step (no lost work, no double work).
  * DBOS.sleep() is a durable nap: the Cloud Run instance may be evicted while
    sleeping (zero cost); it wakes exactly on time.
  * DBOS.recv() blocks durably for the human approver / an event — no polling.
  * DBOS.set_event() publishes status for the Admin UI to read (a projection, never
    the agent itself).

Enterprise 1:1 tier = Temporal (documented, not deployed). See ADR-0021.
"""
from __future__ import annotations

import os

from dbos import DBOS

from evaluator import judge
from generator import run_step
from planner import make_plan

THRESHOLD = float(os.getenv("EVAL_THRESHOLD", "0.6"))
STEP_SLEEP = int(os.getenv("STEP_SLEEP_SECONDS", "5"))
HUMAN_WAIT = int(os.getenv("HUMAN_WAIT_SECONDS", str(7 * 24 * 3600)))  # 1 week
HUMAN_TOPIC = "human_review"


@DBOS.step()
def plan_step(goal: str) -> list[str]:
    return make_plan(goal)


@DBOS.step()
def generate_step(task: str, history: list[dict]) -> str:
    return run_step(task, history)


@DBOS.step()
def evaluate_step(task: str, result: str) -> dict:
    return judge(task, result)


@DBOS.workflow()
def steward_run(goal: str, max_steps: int = 8) -> dict:
    tasks = plan_step(goal)
    history: list[dict] = []
    DBOS.set_event("status", {"phase": "planned", "plan": tasks, "history": history})

    i = 0
    while i < len(tasks) and i < max_steps:
        task = tasks[i]
        result = generate_step(task, history)
        verdict = evaluate_step(task, result)
        entry = {"task": task, "result": result,
                 "score": verdict["score"], "reason": verdict["reason"]}

        if verdict["score"] < THRESHOLD:
            # Escalate. The agent SLEEPS until the approver signals — no polling.
            DBOS.set_event("status", {"phase": "awaiting_human", "step": i,
                                      "pending": entry, "history": history})
            decision = DBOS.recv(HUMAN_TOPIC, timeout_seconds=HUMAN_WAIT)
            if decision is None:
                entry["resolution"] = "timeout_auto_defer"
            elif decision.get("revise"):
                tasks.insert(i + 1, str(decision["revise"]))  # human replan
                entry["resolution"] = "human_revise"
            elif decision.get("approved"):
                entry["resolution"] = "human_approved"
                entry["approver"] = decision.get("approver", "")
                entry["note"] = decision.get("note", "")
            else:
                entry["resolution"] = "human_rejected"
                entry["approver"] = decision.get("approver", "")

        history.append(entry)
        DBOS.set_event("status", {"phase": "working", "step": i, "history": history})
        i += 1

        if i < len(tasks):
            DBOS.sleep(STEP_SLEEP)  # durable nap — safe to scale to zero here

    summary = {"goal": goal, "steps": history, "done": True}
    DBOS.set_event("status", {"phase": "done", "history": history})
    return summary
