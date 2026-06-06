"""
FinChat Loan multi-agent system (Google ADK + Gemini).

Agents (ADR-0004 / docs/04-agent-architecture.md):
  - Planner Agent          : decompose + coordinate
  - Credit Agent           : synthetic credit profile
  - Transaction Review Agent: overdraft history via Transactions DaaS (cross-product)
  - Approval Agent         : risk score + recommendation
  - Notification Agent     : customer comms

Specialists run as a SequentialAgent pipeline (credit -> review -> approval);
the Planner is the root coordinator. Human approval is handled out-of-band by
Cloud Workflows (durable callback) between approval and notification — long-running
state lives in the Workflows execution + BigQuery, not in the agent process.

Offline (ADK not installed), `orchestrate()` runs the same tool sequence so the
logic is exercisable without GCP.
"""
from __future__ import annotations

import os

from tools import (generate_credit_profile, get_overdraft_history,
                   compute_risk, send_notification)

MODEL = os.getenv("AGENT_MODEL", "gemini-2.5-flash")

CREDIT_INSTR = "You are the Credit Agent. Call generate_credit_profile and report the profile. Do not invent numbers."
REVIEW_INSTR = "You are the Transaction Review Agent. Call get_overdraft_history for the account and report overdraft_events."
APPROVAL_INSTR = ("You are the Approval Agent. Call compute_risk with the profile + overdraft_events. "
                  "State the risk_score, recommendation, and reasons. Never decide unilaterally on REVIEW — defer to a human.")
NOTIFY_INSTR = "You are the Notification Agent. Call send_notification with the final decision. Be courteous and concise."
PLANNER_INSTR = ("You are the Planner Agent for loan processing. Validate the request has name, amount, and term. "
                 "If anything is missing, ask for it. Otherwise coordinate the credit, transaction-review, and approval "
                 "specialists, then summarize the recommendation for the human approver.")

try:
    from google.adk.agents import Agent, SequentialAgent

    credit_agent = Agent(name="credit_agent", model=MODEL, instruction=CREDIT_INSTR,
                         description="Generates a synthetic credit profile.",
                         tools=[generate_credit_profile])

    transaction_review_agent = Agent(name="transaction_review_agent", model=MODEL, instruction=REVIEW_INSTR,
                                     description="Reviews overdraft history from the transactions product.",
                                     tools=[get_overdraft_history])

    approval_agent = Agent(name="approval_agent", model=MODEL, instruction=APPROVAL_INSTR,
                           description="Computes risk score and recommendation.",
                           tools=[compute_risk])

    notification_agent = Agent(name="notification_agent", model=MODEL, instruction=NOTIFY_INSTR,
                               description="Notifies the customer of the decision.",
                               tools=[send_notification])

    # Specialist pipeline: credit -> review -> approval (deterministic order).
    underwriting_pipeline = SequentialAgent(
        name="underwriting_pipeline",
        sub_agents=[credit_agent, transaction_review_agent, approval_agent],
    )

    # Planner coordinates the pipeline + notification.
    root_agent = Agent(
        name="loan_planner",
        model=MODEL,
        instruction=PLANNER_INSTR,
        description="Coordinates the loan underwriting pipeline and customer notification.",
        sub_agents=[underwriting_pipeline, notification_agent],
    )
except Exception:  # pragma: no cover - ADK not installed (offline dev)
    root_agent = None


def orchestrate(loan_id: str, customer_name: str, amount: float, term_months: int,
                account_id: str | None = None) -> dict:
    """Deterministic offline orchestration mirroring the agent pipeline."""
    profile = generate_credit_profile(loan_id, amount, term_months)
    overdraft = get_overdraft_history(account_id or "")
    risk = compute_risk(loan_id, amount, term_months, profile["credit_score"],
                        profile["annual_income"], profile["existing_debt"],
                        profile["dti_ratio"], overdraft["overdraft_events"])
    return {"loan_id": loan_id, "profile": profile, "overdraft": overdraft, "risk": risk,
            "awaiting": "human_approval"}


if __name__ == "__main__":
    import json
    print(json.dumps(orchestrate("loan-demo-1", "Jeremy D", 15000, 36, "acct-001"), indent=2))
