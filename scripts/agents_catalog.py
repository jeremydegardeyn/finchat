#!/usr/bin/env python3
"""
Single source of truth for the FinChat **agent registry** (ADR-0023).

Why this exists
---------------
The model inventory (docs/19) registers what *reasons*. This registers what *acts*.
Revised model risk guidance (SR 26-2 / OCC 2026-13, April 2026) places generative and
agentic AI outside supervisory model-risk scope, so there is no external framework that
requires an agent inventory. FinChat keeps one anyway, because the question examination
and incident response both ask — "who authorised this agent to take this action, and who
is accountable for it" — has to be answerable at any point.

What makes this a control rather than a list
--------------------------------------------
1. Every agent gets a **distinct service account**, never a shared one, so its actions are
   individually attributable in the audit trail.
2. The `tools` list is an **allow-list that CI enforces against the code**. Add a tool to
   an agent without registering it and `verify_agent_registry.py` fails the build. That is
   the difference between a registry that records intent and one that governs behaviour.
3. Every agent carries a named accountable **human owner** and a **recertification date**
   on the same cycle as privileged human access. Overdue recertification fails the build.

Consumers
---------
- `verify_agent_registry.py`  — the CI gate (drift, completeness, recertification)
- `agent_registry_bootstrap.py` — publishes rows to BigQuery `finchat_platform_<env>.agent_registry`
- `infra/modules/agent_registry` — per-agent service accounts, via generated tfvars

Regenerate the Terraform input after any change here:
    python scripts/agents_catalog.py --emit-tfvars infra/envs/<env>/agents.auto.tfvars.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

STEWARD = "data-steward@datadinosaur.com"
PLATFORM_OWNER = "platform-ai@datadinosaur.com"

# Recertification cadence by risk tier — mirrors the privileged-human-access cycle.
RECERT_DAYS = {"HIGH": 90, "MEDIUM": 180, "LOW": 365}

# Model pinning (ADR-0022). `alias` is what the code requests; the *served* version is
# read back from the Vertex response `modelVersion` field and logged per turn, because a
# provider-side change to an alias is exactly the failure mode pinning exists to catch.
# Set FINCHAT_MODEL_PIN to a Model Garden snapshot id to move an agent off the alias;
# until then verify_agent_registry.py reports the agent as UNPINNED (a finding, not a
# build failure — the alias is a legitimate posture as long as it is a declared one).
DEFAULT_MODEL_ALIAS = "gemini-2.5-flash"


def agents(env: str) -> list[dict]:
    """Return the canonical agent list for an environment.

    `tools` is the authoritative permission scope: the tool functions this agent is
    allowed to invoke. CI asserts it matches what the code actually passes to the agent
    constructor. `consequential` marks agents whose action has a side effect on financial
    data or on a customer; every consequential agent must declare a human-in-the-loop gate.
    """
    return [
        # --- Transactions product -------------------------------------------------
        {
            "id": "banking_assistant",
            "display": "Banking Assistant",
            "product": "transactions",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-agent",
            "source": "products/transactions/agent/agent.py",
            "code_name": "finchat_banking_assistant",
            "owner": "transactions-product@datadinosaur.com",
            "business_area": "Retail Deposits",
            "risk_tier": "HIGH",  # customer-facing, real account data
            "sa_key": "agent-banking-assistant",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": [
                "get_account_balance",
                "get_transaction_history",
                "get_account_summary",
                "get_loan_status",
                "search_knowledge_base",
            ],
            "data_scope": "Caller's own account only (DaaS API enforces account scoping); "
                          "knowledge base corpus (public policy/product documents).",
            "consequential": False,  # read-only; answers questions, changes nothing
            "hitl": False,
            "model_ref": "M2",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        # --- Loans product --------------------------------------------------------
        {
            "id": "loan_planner",
            "display": "Loan Planner (root coordinator)",
            "product": "loans",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-loan-agent",
            "source": "products/loans/agents/agents.py",
            "code_name": "loan_planner",
            "owner": "loans-product@datadinosaur.com",
            "business_area": "Consumer Lending",
            "risk_tier": "HIGH",
            "sa_key": "agent-loan-planner",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": [],  # coordinates sub-agents; holds no tools of its own
            "sub_agents": ["credit_agent", "transaction_review_agent",
                           "approval_agent", "notification_agent"],
            "data_scope": "Loan application payload only; delegates all data access to specialists.",
            "consequential": False,
            "hitl": True,  # the pipeline it coordinates ends at a human approver
            "model_ref": "M3",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        {
            "id": "credit_agent",
            "display": "Credit Agent",
            "product": "loans",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-loan-agent",
            "source": "products/loans/agents/agents.py",
            "code_name": "credit_agent",
            "owner": "loans-product@datadinosaur.com",
            "business_area": "Consumer Lending",
            "risk_tier": "HIGH",  # feeds a credit decision
            "sa_key": "agent-credit",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": ["generate_credit_profile"],
            "data_scope": "Synthetic credit profile generation for the loan under review.",
            "consequential": False,
            "hitl": True,
            "model_ref": "M3",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        {
            "id": "transaction_review_agent",
            "display": "Transaction Review Agent",
            "product": "loans",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-loan-agent",
            "source": "products/loans/agents/agents.py",
            "code_name": "transaction_review_agent",
            "owner": "loans-product@datadinosaur.com",
            "business_area": "Consumer Lending",
            "risk_tier": "HIGH",
            "sa_key": "agent-txn-review",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": ["get_overdraft_history"],
            # Cross-product read: this is the one loan agent that reaches into another
            # product's data, so its scope is called out explicitly.
            "data_scope": "Overdraft history for the applicant's account, via the Transactions "
                          "DaaS API (cross-product read — see docs/lineage).",
            "consequential": False,
            "hitl": True,
            "model_ref": "M3",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        {
            "id": "approval_agent",
            "display": "Approval Agent",
            "product": "loans",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-loan-agent",
            "source": "products/loans/agents/agents.py",
            "code_name": "approval_agent",
            "owner": "loans-product@datadinosaur.com",
            "business_area": "Consumer Lending",
            "risk_tier": "HIGH",  # produces the recommendation a human decides on
            "sa_key": "agent-approval",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": ["compute_risk"],
            "data_scope": "Credit profile + overdraft events for the loan under review.",
            "consequential": False,  # recommends; the human approver decides (ADR-0016)
            "hitl": True,
            "model_ref": "M3",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        {
            "id": "notification_agent",
            "display": "Notification Agent",
            "product": "loans",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-loan-agent",
            "source": "products/loans/agents/agents.py",
            "code_name": "notification_agent",
            "owner": "loans-product@datadinosaur.com",
            "business_area": "Consumer Lending",
            "risk_tier": "MEDIUM",  # customer comms, post-decision
            "sa_key": "agent-notification",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": ["send_notification"],
            "data_scope": "Final decision record for the loan; customer contact details.",
            "consequential": True,  # sends a message to a real customer
            "hitl": True,  # only runs after the approver's decision is recorded
            "model_ref": "M3",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        # --- Reconciliation Steward (durable harness, ADR-0021) -------------------
        {
            "id": "steward_planner",
            "display": "Steward Planner",
            "product": "steward",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-steward",
            "source": "products/steward/harness/planner.py",
            "code_name": "plan",
            "owner": STEWARD,
            "business_area": "Data Governance",
            "risk_tier": "MEDIUM",
            "sa_key": "agent-steward-planner",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": ["read_findings"],
            "data_scope": "Dataplex data-quality findings; no row-level financial data.",
            "consequential": False,
            "hitl": False,
            "model_ref": "M6",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        {
            "id": "steward_generator",
            "display": "Steward Generator (remediation proposer)",
            "product": "steward",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-steward",
            "source": "products/steward/harness/generator.py",
            "code_name": "propose",
            "owner": STEWARD,
            "business_area": "Data Governance",
            "risk_tier": "MEDIUM",
            "sa_key": "agent-steward-generator",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": [],  # proposes text; the harness applies, never the agent
            "data_scope": "The DQ finding under remediation and prior-step history.",
            "consequential": False,
            "hitl": True,  # every proposal pauses for the verified approver
            "model_ref": "M6",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        {
            "id": "steward_evaluator",
            "display": "Steward Evaluator (inline judge gate)",
            "product": "steward",
            "kind": "llm_agent",
            "runtime": f"cloud-run:finchat-{env}-steward",
            "source": "products/steward/harness/evaluator.py",
            "code_name": "assess",
            "owner": PLATFORM_OWNER,
            "business_area": "Platform / AgentOps",
            "risk_tier": "MEDIUM",  # a control model — its drift degrades the gate
            "sa_key": "agent-steward-evaluator",
            "model_alias": DEFAULT_MODEL_ALIAS,
            "tools": [],
            "data_scope": "The proposal under assessment; no direct data access.",
            "consequential": False,
            "hitl": True,
            "model_ref": "M6",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
        # --- Analyst surface (managed) --------------------------------------------
        {
            "id": "analyst_data_agent",
            "display": "Analyst Conversational Data Agent",
            "product": "analyst",
            "kind": "managed_agent",  # Gemini Data Analytics dataAgents resource
            "runtime": f"gemini-data-analytics:finchat-{env}-analyst",
            "source": "ui/server.py",
            "code_name": "finchat-analyst",
            "owner": PLATFORM_OWNER,
            "business_area": "Data & Analytics",
            "risk_tier": "MEDIUM",
            # Runs under the *end user's* propagated credentials (ADR-0019) rather than a
            # platform SA, so the identity that acts is the analyst's own. The SA below is
            # the fallback identity used only when credential propagation is unavailable.
            "sa_key": "agent-analyst-fallback",
            "model_alias": "managed",  # version governed by the managed service
            "tools": ["conversational_analytics_chat"],
            "data_scope": "Semantic perimeter only — dim_customer / dim_account / "
                          "fact_transaction / customer_360, identifier columns structurally "
                          "absent (ADR-0018). End-user IAM + policy tags apply (ADR-0019).",
            "consequential": False,
            "hitl": False,
            "model_ref": "M5",
            "registered": "2026-08-04",
            "last_recertified": "2026-08-04",
            "status": "active",
        },
    ]


# --- Derived helpers ---------------------------------------------------------

def recert_due(agent: dict) -> date:
    """Date this agent's identity must be recertified by."""
    last = date.fromisoformat(agent["last_recertified"])
    return last + timedelta(days=RECERT_DAYS[agent["risk_tier"]])


def service_account_id(agent: dict, env: str, prefix: str = "finchat") -> str:
    """GCP service account id. Capped at 30 chars (IAM limit)."""
    return f"{prefix}-{env}-{agent['sa_key']}"[:30].rstrip("-")


def emit_tfvars(env: str) -> dict:
    """Terraform input for infra/modules/agent_registry.

    Generated rather than hand-maintained so the SA set cannot drift from the registry.
    """
    return {
        "agents": {
            a["id"]: {
                "sa_key": a["sa_key"],
                "display": a["display"],
                "owner": a["owner"],
                "risk_tier": a["risk_tier"],
                "product": a["product"],
                "recert_due": recert_due(a).isoformat(),
            }
            for a in agents(env) if a["status"] == "active"
        }
    }


def main() -> int:
    p = argparse.ArgumentParser(description="FinChat agent registry — source of truth.")
    p.add_argument("--env", default="dev")
    p.add_argument("--emit-tfvars", metavar="PATH",
                   help="write the Terraform agents map to PATH (.auto.tfvars.json)")
    p.add_argument("--list", action="store_true", help="print the registry as JSON")
    args = p.parse_args()

    if args.emit_tfvars:
        with open(args.emit_tfvars, "w", encoding="utf-8") as fh:
            json.dump(emit_tfvars(args.env), fh, indent=2)
            fh.write("\n")
        print(f"wrote {args.emit_tfvars}")
        return 0

    if args.list:
        print(json.dumps(agents(args.env), indent=2))
        return 0

    # Default: a human-readable census.
    rows = agents(args.env)
    print(f"FinChat agent registry — {args.env} ({len(rows)} agents)\n")
    for a in rows:
        tools = ", ".join(a["tools"]) or "—"
        print(f"  {a['id']:<26} {a['risk_tier']:<7} {a['owner']:<38} "
              f"recert {recert_due(a).isoformat()}")
        print(f"  {'':<26} tools: {tools}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
