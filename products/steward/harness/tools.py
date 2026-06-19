"""Steward tools — the durable agent's hands.

In a live deploy these call the existing FinChat surfaces:
  * the Transactions/Loans **DaaS APIs** (balances, history, loan status),
  * **BigQuery** data-quality / reconciliation queries against the gold tables,
  * the **Dataplex data contracts** (`contracts/*.yaml`) the steward checks against.

Kept deterministic + dependency-free here so the harness runs offline (same
discipline as the loan agent's offline `orchestrate()`).
"""
from __future__ import annotations

import os

_CONTRACTS_DIR = os.getenv("CONTRACTS_DIR", "contracts")


def list_contracts() -> list[str]:
    """Discover the data contracts the steward should check (offline-safe)."""
    try:
        return sorted(f for f in os.listdir(_CONTRACTS_DIR) if f.endswith((".yaml", ".yml")))
    except OSError:
        # Offline default: the five published FinChat data products.
        return ["transactions.yaml", "loans.yaml", "accounts.yaml",
                "customers.yaml", "graph.yaml"]


def run_dq_check(contract: str) -> str:
    """Pretend to run a BigQuery data-quality / reconciliation check.

    Live impl: SELECT against the gold view + compare to the contract's freshness,
    completeness, and value constraints. Returns a short finding string.
    """
    return (f"(tool:run_dq_check) {contract}: rows reconciled, freshness OK, "
            f"0 null-key violations, 2 amounts outside the expected band.")
