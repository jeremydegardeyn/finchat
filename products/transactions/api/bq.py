"""
Data access for the Transactions DaaS API.

Reads the BigQuery Gold/Silver serving layer. Falls back to an in-memory sample
dataset when BigQuery is unavailable (no project / no credentials) so the API
runs locally for development and demos (DEMO_MODE).
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache

PROJECT = os.getenv("GCP_PROJECT", "")
GOLD = os.getenv("GOLD_DATASET", "finchat_gold_dev")
SILVER = os.getenv("SILVER_DATASET", "finchat_silver_dev")
DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")


class Repository:
    """Serving-layer queries with a demo fallback."""

    def __init__(self):
        self._client = None
        self._demo = DEMO_MODE
        if not self._demo:
            try:
                from google.cloud import bigquery
                if not PROJECT:
                    raise RuntimeError("GCP_PROJECT not set")
                self._client = bigquery.Client(project=PROJECT)
            except Exception:
                # No creds / project -> degrade gracefully to demo data.
                self._demo = True

    @property
    def mode(self) -> str:
        return "demo" if self._demo else "bigquery"

    # --- queries -------------------------------------------------------------
    def get_balance(self, account_id: str) -> dict | None:
        if self._demo:
            return _demo().get_balance(account_id)
        sql = f"""
          SELECT account_id, currency, balance, last_activity_at
          FROM `{PROJECT}.{GOLD}.account_balance`
          WHERE account_id = @account_id
        """
        return _one(self._run(sql, account_id))

    def get_summary(self, account_id: str) -> dict | None:
        if self._demo:
            return _demo().get_summary(account_id)
        sql = f"""
          SELECT * FROM `{PROJECT}.{GOLD}.account_summary`
          WHERE account_id = @account_id
        """
        return _one(self._run(sql, account_id))

    def get_transactions(self, account_id: str, limit: int = 50) -> list[dict]:
        if self._demo:
            return _demo().get_transactions(account_id, limit)
        sql = f"""
          SELECT transaction_id, txn_type, amount, currency, status, event_time
          FROM `{PROJECT}.{SILVER}.transaction`
          WHERE account_id = @account_id
          ORDER BY event_time DESC
          LIMIT @limit
        """
        return self._run(sql, account_id, limit)

    def get_recent_activity(self, account_id: str, days: int = 30) -> list[dict]:
        if self._demo:
            return _demo().get_recent_activity(account_id, days)
        sql = f"""
          SELECT transaction_id, txn_type, amount, currency, status, event_time
          FROM `{PROJECT}.{SILVER}.transaction`
          WHERE account_id = @account_id
            AND event_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
          ORDER BY event_time DESC
        """
        return self._run(sql, account_id, days_param=days)

    # --- helpers -------------------------------------------------------------
    def _run(self, sql, account_id, limit=None, days_param=None):
        from google.cloud import bigquery
        params = [bigquery.ScalarQueryParameter("account_id", "STRING", account_id)]
        if limit is not None:
            params.append(bigquery.ScalarQueryParameter("limit", "INT64", limit))
        if days_param is not None:
            params.append(bigquery.ScalarQueryParameter("days", "INT64", days_param))
        job = self._client.query(
            sql,
            job_config=bigquery.QueryJobConfig(
                query_parameters=params,
                # cost guardrail: cap bytes billed per request
                maximum_bytes_billed=10 * 1024**3,
            ),
        )
        return [_jsonable(dict(row)) for row in job.result()]


def _jsonable(row: dict) -> dict:
    """Coerce BigQuery native types to JSON/Pydantic-friendly ones.

    NUMERIC -> float, TIMESTAMP/DATE -> ISO string. The Pydantic response models
    expect float/str, so without this the API raises ResponseValidationError.
    """
    from datetime import date, datetime
    from decimal import Decimal
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _one(rows):
    return rows[0] if rows else None


# --- in-memory demo dataset --------------------------------------------------
@lru_cache(maxsize=1)
def _demo():
    return _DemoData()


class _DemoData:
    """Deterministic sample data keyed by 'acct-001' / 'acct-002' / 'acct-003'."""

    def __init__(self):
        rng = random.Random(2026)
        self.accounts = {}
        for i in range(1, 4):
            aid = f"acct-{i:03d}"
            txns = []
            bal = 0.0
            for j in range(random.Random(i).randint(8, 20)):
                ttype = rng.choice(["DEPOSIT", "WITHDRAWAL", "WITHDRAWAL", "FEE", "TRANSFER"])
                amt = round(rng.uniform(10, 1500), 2)
                bal += amt if ttype == "DEPOSIT" else -amt
                txns.append({
                    "transaction_id": f"{aid}-tx-{j}",
                    "txn_type": ttype,
                    "amount": amt,
                    "currency": "USD",
                    "status": "POSTED",
                    "event_time": (datetime.now(timezone.utc) - timedelta(days=j * 2)).isoformat(),
                })
            self.accounts[aid] = {"txns": txns, "balance": round(bal, 2)}

    def get_balance(self, aid):
        a = self.accounts.get(aid)
        if not a:
            return None
        return {"account_id": aid, "currency": "USD", "balance": a["balance"],
                "last_activity_at": a["txns"][0]["event_time"] if a["txns"] else None}

    def get_summary(self, aid):
        a = self.accounts.get(aid)
        if not a:
            return None
        t = a["txns"]
        return {
            "account_id": aid, "customer_id": f"cust-{aid[-3:]}", "account_type": "CHECKING",
            "currency": "USD", "status": "ACTIVE",
            "deposit_count": sum(x["txn_type"] == "DEPOSIT" for x in t),
            "withdrawal_count": sum(x["txn_type"] == "WITHDRAWAL" for x in t),
            "fee_count": sum(x["txn_type"] == "FEE" for x in t),
            "net_balance": a["balance"],
            "last_activity_at": t[0]["event_time"] if t else None,
        }

    def get_transactions(self, aid, limit=50):
        a = self.accounts.get(aid)
        return (a["txns"][:limit] if a else [])

    def get_recent_activity(self, aid, days=30):
        return self.get_transactions(aid, 50)
