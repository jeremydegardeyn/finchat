#!/usr/bin/env python3
"""
FinChat AgentOps evaluation pipeline (offline).

Computes the enterprise agent metrics against the deterministic logic so the
suite runs with no GCP/LLM cost in CI:

  * Grounding accuracy        — answers only assert values present in tool output
  * Hallucination rate        — fraction of answers asserting unsupported facts
  * Tool utilization          — correct tool selected for the intent
  * Response quality          — heuristic (relevance + policy compliance)
  * Approval rec. accuracy    — loan recommendations vs. labeled ground truth

For the *live* agents, the same datasets feed Vertex AI eval (see vertex_eval.py);
this offline harness validates the grounding contract + decision logic and gates CI.

Usage: python evaluate.py [--out ../reports/latest.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(__file__)
DATASETS = os.path.join(HERE, "..", "datasets")

# Import grounding tools + risk logic from the products.
sys.path.insert(0, os.path.join(HERE, "..", "..", "products", "transactions", "agent"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "products", "loans", "api"))
os.environ.setdefault("DEMO_MODE", "1")
import tools as txn_tools          # noqa: E402
from risk import synthesize_credit_profile, score_risk, CreditProfile  # noqa: E402


def load(name):
    with open(os.path.join(DATASETS, name), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


# --------------------------------------------------------------------------- #
# Transaction agent: instrumented responder (records tool used + answer)
# --------------------------------------------------------------------------- #
def respond(query: str, account_id: str | None):
    """Mirror the agent's grounded routing; return (tool_used, answer, facts)."""
    q = query.lower()
    if not account_id:
        return None, "Please provide your account id before I can help.", []
    if re.search(r"invest|stock|advice", q):
        return None, "I can't provide investment advice, but I can share account data.", []
    # Cross-customer guard: refuse if the query references any account != the caller's.
    mentioned = set(re.findall(r"acct-\d+", q))
    if any(m != account_id for m in mentioned):
        return None, "I can't reveal another customer's data.", []
    if re.search(r"balance|how much", q):
        b = txn_tools.get_account_balance(account_id)
        if "error" in b:
            return "get_account_balance", f"Account {account_id} was not found.", []
        return "get_account_balance", f"Your balance on {account_id} is {b['balance']} {b['currency']}.", [b["balance"]]
    if re.search(r"summary|overview", q):
        s = txn_tools.get_account_summary(account_id)
        if "error" in s:
            return "get_account_summary", f"No data for {account_id}.", []
        return "get_account_summary", f"{account_id}: {s['deposit_count']} deposits, net {s['net_balance']}.", [s["net_balance"], s["deposit_count"]]
    if re.search(r"transaction|recent|spent|activity", q):
        t = txn_tools.get_transaction_history(account_id, 5)
        facts = [x["amount"] for x in t]
        return "get_transaction_history", "Recent: " + ", ".join(f"{x['txn_type']} {x['amount']}" for x in t), facts
    return None, "I can share your balance, recent transactions, or a summary.", []


def number_tokens(text):
    return set(re.findall(r"-?\d+\.?\d*", text))


def eval_transaction_agent():
    rows = load("transaction_agent_eval.jsonl")
    tool_ok = grounded_ok = quality_ok = hallucinated = 0
    cases = []
    for r in rows:
        tool, answer, facts = respond(r["query"], r.get("account_id"))
        # tool utilization
        t_ok = (tool == r["expected_tool"])
        # grounding: every number asserted must come from tool facts
        fact_str = {str(f) for f in facts}
        nums = number_tokens(answer)
        unsupported = {n for n in nums if n not in fact_str and not _is_id_num(n, r.get("account_id"))}
        g_ok = len(unsupported) == 0
        if not g_ok:
            hallucinated += 1
        # quality: refusal/ask cases must not call a tool; data cases must answer
        if r["expected_tool"] is None:
            q_ok = tool is None
        else:
            q_ok = tool is not None and len(answer) > 0
        tool_ok += t_ok; grounded_ok += g_ok; quality_ok += q_ok
        cases.append({"id": r["id"], "tool_ok": t_ok, "grounded": g_ok, "quality_ok": q_ok})
    n = len(rows)
    return {
        "n": n,
        "grounding_accuracy": round(grounded_ok / n, 3),
        "hallucination_rate": round(hallucinated / n, 3),
        "tool_utilization": round(tool_ok / n, 3),
        "response_quality": round(quality_ok / n, 3),
        "cases": cases,
    }


def _is_id_num(token, account_id):
    return bool(account_id) and token in account_id


def eval_loan_recommendations():
    rows = load("loan_eval.jsonl")
    correct = 0
    cases = []
    for r in rows:
        p = CreditProfile("eval", r["credit_score"], r["annual_income"], r["existing_debt"], r["dti_ratio"])
        res = score_risk(p, r["amount"], overdraft_events=r["overdraft_events"])
        ok = res.recommendation == r["expected_recommendation"]
        correct += ok
        cases.append({"id": r["id"], "expected": r["expected_recommendation"],
                      "got": res.recommendation, "score": res.risk_score, "ok": ok})
    n = len(rows)
    return {"n": n, "approval_recommendation_accuracy": round(correct / n, 3), "cases": cases}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "reports", "latest.json"))
    args = ap.parse_args()

    txn = eval_transaction_agent()
    loan = eval_loan_recommendations()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_agent": txn,
        "loan_recommendations": loan,
        "summary": {
            "grounding_accuracy": txn["grounding_accuracy"],
            "hallucination_rate": txn["hallucination_rate"],
            "tool_utilization": txn["tool_utilization"],
            "response_quality": txn["response_quality"],
            "approval_recommendation_accuracy": loan["approval_recommendation_accuracy"],
        },
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    s = report["summary"]
    print("=== FinChat AgentOps Evaluation ===")
    for k, v in s.items():
        print(f"  {k:34s}: {v}")
    print(f"  report -> {os.path.relpath(args.out, HERE)}")

    # CI gate: fail if quality thresholds not met.
    thresholds = {"grounding_accuracy": 0.9, "tool_utilization": 0.9,
                  "approval_recommendation_accuracy": 0.8}
    failed = {k: (s[k], t) for k, t in thresholds.items() if s[k] < t}
    if failed:
        print("THRESHOLD FAILURES:", failed)
        return 1
    if s["hallucination_rate"] > 0.05:
        print("HALLUCINATION RATE TOO HIGH:", s["hallucination_rate"])
        return 1
    print("All thresholds passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
