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
LOAN_BASE = os.getenv("LOAN_API_URL", "")
_TIMEOUT = 8.0

# RAG knowledge base (BigQuery vector store).
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT", "")
KB_DATASET = os.getenv("KB_DATASET", "")


def _id_token(audience: str):
    """Mint a Google-signed OIDC ID token so we can call a private Cloud Run service."""
    if not audience.startswith("https://"):
        return None  # local/demo (http) — no auth needed
    try:
        from google.auth.transport.requests import Request
        import google.oauth2.id_token as idt
        return idt.fetch_id_token(Request(), audience)
    except Exception:
        return None


def _get(path: str, base: str | None = None):
    import httpx
    base = base or API_BASE
    headers = {}
    token = _id_token(base)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(f"{base}{path}", headers=headers, timeout=_TIMEOUT)
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


def get_loan_status(loan_id: str) -> dict:
    """Get the status of a customer's loan request: current status, risk
    recommendation, and the decision history (approve/reject/modify/counteroffer).

    Args:
        loan_id: The loan identifier returned when the request was submitted
                 (looks like 'loan-...'). Ask the customer for it if unknown.
    Returns:
        The loan record (status, amount, term, risk, decisions) or an error.
    """
    if not LOAN_BASE:
        return {"error": "loan service not configured"}
    try:
        return _get(f"/v1/loans/{loan_id}", base=LOAN_BASE)
    except Exception:
        return {"error": f"loan {loan_id} not found"}


def search_knowledge_base(query: str) -> list[dict]:
    """Search FinChat Bank's knowledge base for policies, terms & conditions, fees,
    branch locations & hours, and lending info. Use this for general bank questions
    (NOT for a customer's own balance/transactions — use the account tools for those).

    Args:
        query: A natural-language question, e.g. 'what are the overdraft fees?' or
               'when is the Lakewood branch open?'.
    Returns:
        A list of the most relevant knowledge snippets (title, category, content).
        Ground your answer in these snippets; if empty, say you don't have that info.
    """
    if not (PROJECT and KB_DATASET):
        return [{"error": "knowledge base not configured"}]
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=PROJECT)
        sql = f"""
          SELECT base.title AS title, base.category AS category, base.content AS content
          FROM VECTOR_SEARCH(
            TABLE `{PROJECT}.{KB_DATASET}.kb_chunks`, 'embedding',
            (SELECT ml_generate_embedding_result AS embedding
             FROM ML.GENERATE_EMBEDDING(
               MODEL `{PROJECT}.{KB_DATASET}.embedding_model`,
               (SELECT @q AS content),
               STRUCT(TRUE AS flatten_json_output))),
            top_k => 4, distance_type => 'COSINE')
        """
        job = client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("q", "STRING", query)],
            maximum_bytes_billed=2 * 1024**3,
        ))
        return [{"title": r["title"], "category": r["category"], "content": r["content"]}
                for r in job.result()]
    except Exception as e:
        return [{"error": f"knowledge base unavailable: {type(e).__name__}"}]


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
