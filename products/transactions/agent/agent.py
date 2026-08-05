"""
FinChat Banking Assistant — conversational data agent (Google ADK + Gemini).

Enterprise agent pattern (ADR-0004): authored in ADK, deployable to Vertex AI
Agent Engine (managed sessions, tracing, eval) or Cloud Run (portable fallback).
Tool-calling grounds every answer in the governed Transactions data product.

The module exposes `root_agent` (ADK's discovery convention) so it runs under:
    adk run .        # local interactive
    adk web          # local web UI
and deploys to Agent Engine via deploy.py.
"""
from __future__ import annotations

import os

from tools import (get_account_balance, get_transaction_history, get_account_summary,
                   get_loan_status, search_knowledge_base)

# Model pin (ADR-0022). FINCHAT_PIN_AGENT carries a Model Garden snapshot id when one is
# configured; otherwise this runs the floating alias and the registry gate reports PIN-1.
# An alias is a legitimate posture — an undeclared one is not.
MODEL = os.getenv("FINCHAT_PIN_AGENT") or os.getenv("AGENT_MODEL", "gemini-2.5-flash")

INSTRUCTION = """\
You are FinChat, a retail-banking assistant for a regulated financial institution.

Rules of engagement:
- ALWAYS ground answers in tool results. Never invent balances, amounts, or transactions.
- If the user has not provided an account id, ask for it before calling tools.
- Use get_account_balance for "how much do I have", get_transaction_history for
  "what did I spend / recent transactions", and get_account_summary for overviews.
- For "what's the status of my loan / loan application", call get_loan_status with
  the loan id (ask for it if the customer hasn't given one); report status, the
  risk recommendation, and any decision.
- For general bank questions — fees, overdraft/funds/privacy policy, terms &
  conditions, branch locations & hours, ATMs, lending/rates — call
  search_knowledge_base and ground your answer ONLY in the returned snippets.
  If the snippets don't cover it, say you don't have that information.
- If a tool returns an error or no data, say so plainly; do not fabricate.
- Be concise, professional, and never reveal another customer's data.
- Do not provide financial, tax, or investment advice; stick to factual account data.
"""

try:
    # Real ADK path.
    from google.adk.agents import Agent

    from gateway_llm import gateway_model

    root_agent = Agent(
        name="finchat_banking_assistant",
        # Routed through the Enterprise AI Gateway when configured (ADR-0024); falls back
        # to the plain model string otherwise, so the agent runs either way. agent_id ties
        # this call to its registry entry — owner, risk tier, tool allow-list, recert date.
        model=gateway_model("banking_assistant", MODEL,
                            owner="transactions-product@datadinosaur.com"),
        description="Answers account questions grounded in the banking transaction data product.",
        instruction=INSTRUCTION,
        tools=[get_account_balance, get_transaction_history, get_account_summary,
               get_loan_status, search_knowledge_base],
    )
except Exception:  # pragma: no cover - ADK not installed (offline dev)
    # Lightweight stand-in so `python agent.py` works without the ADK installed:
    # naive intent routing over the same grounding tools (NOT for production).
    root_agent = None

    def answer(query: str, account_id: str | None = None) -> str:
        q = query.lower()
        if not account_id:
            return "Please provide your account id (e.g. acct-001)."
        if "balance" in q or "how much" in q:
            b = get_account_balance(account_id)
            return f"Balance for {account_id}: {b.get('balance')} {b.get('currency')}" if "error" not in b else b["error"]
        if "summary" in q or "overview" in q:
            return str(get_account_summary(account_id))
        if "transaction" in q or "spent" in q or "recent" in q or "activity" in q:
            return str(get_transaction_history(account_id, 5))
        return "I can share your balance, recent transactions, or an account summary."

    if __name__ == "__main__":
        print(answer("what is my balance?", "acct-001"))
        print(answer("show recent transactions", "acct-001"))
