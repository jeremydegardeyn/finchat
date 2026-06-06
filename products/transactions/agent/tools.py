"""
Grounding tools for the FinChat banking assistant.

Each tool calls the Transactions DaaS API (enterprise grounding: the agent reads
the same governed data products as every other consumer). If the API is
unreachable, tools fall back to the in-memory demo repository so the agent runs
offline for development and evaluation.
"""
from __future__ import annotations

import os

API_BASE = os.getenv("TXN_API_URL", "http://localhost:8080")
_TIMEOUT = 8.0


def _get(path: str):
    import httpx
    resp = httpx.get(f"{API_BASE}{path}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fallback_repo():
    # Reuse the API's demo data layer for offline grounding.
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
    os.environ.setdefault("DEMO_MODE", "1")
    from bq import Repository
    return Repository()


def get_account_balance(account_id: str) -> dict:
    """Return the current balance for a bank account.

    Args:
        account_id: The account identifier, e.g. 'acct-001'.
    Returns:
        A dict with account_id, currency, balance, and last_activity_at.
    """
    try:
        return _get(f"/v1/accounts/{account_id}/balance")
    except Exception:
        row = _fallback_repo().get_balance(account_id)
        return row or {"error": f"account {account_id} not found"}


def get_transaction_history(account_id: str, limit: int = 10) -> list[dict]:
    """Return recent transactions for an account, most recent first.

    Args:
        account_id: The account identifier.
        limit: Max number of transactions to return (1-50).
    Returns:
        A list of transactions (type, amount, currency, status, time).
    """
    limit = max(1, min(limit, 50))
    try:
        return _get(f"/v1/accounts/{account_id}/transactions?limit={limit}")
    except Exception:
        return _fallback_repo().get_transactions(account_id, limit)


def get_account_summary(account_id: str) -> dict:
    """Return an account summary: activity counts and net balance.

    Args:
        account_id: The account identifier.
    Returns:
        A dict with deposit/withdrawal/fee counts and net_balance.
    """
    try:
        return _get(f"/v1/accounts/{account_id}/summary")
    except Exception:
        row = _fallback_repo().get_summary(account_id)
        return row or {"error": f"account {account_id} not found"}
