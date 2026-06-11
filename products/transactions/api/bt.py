"""
Bigtable hot-path reads for the DaaS API (ADR-0017).

When BIGTABLE_INSTANCE is set, balance and recent-transaction lookups are served
from Bigtable at single-digit-ms point-read/prefix-scan latency; BigQuery remains
the analytical source of truth (and the fallback when unset/unavailable).

Row-key design (the part that matters):
  txn_by_account : {account_id}#{reverse_ts}#{txn_id8}
      - account_id prefix -> "recent txns for account" is ONE prefix scan
      - reverse_ts (9999999999 - epoch_seconds, zero-padded) -> newest sorts first,
        so `limit N` short-circuits the scan after N rows
      - txn_id suffix -> uniqueness when two events share a second
      - high-cardinality leading component -> writes spread across tablets (no
        hotspotting; never lead with a timestamp)
  account_balance : {account_id}   (pure point read; GC keeps 1 version)

Works against the emulator via BIGTABLE_EMULATOR_HOST (free local dev).
"""
from __future__ import annotations

import os

BT_INSTANCE = os.getenv("BIGTABLE_INSTANCE", "")
BT_PROJECT = os.getenv("GCP_PROJECT", "") or os.getenv("GOOGLE_CLOUD_PROJECT", "")
REVERSE_EPOCH = 9_999_999_999  # seconds; keys sort newest-first


def enabled() -> bool:
    return bool(BT_INSTANCE)


_tables: dict = {}


def _table(name: str):
    if name not in _tables:
        from google.cloud import bigtable
        client = bigtable.Client(project=BT_PROJECT or "emulator", admin=False)
        _tables[name] = client.instance(BT_INSTANCE).table(name)
    return _tables[name]


def reverse_ts(epoch_seconds: int) -> str:
    return str(REVERSE_EPOCH - int(epoch_seconds)).zfill(10)


def txn_row_key(account_id: str, epoch_seconds: int, txn_id: str) -> str:
    return f"{account_id}#{reverse_ts(epoch_seconds)}#{txn_id[:8]}"


def _cell(row, family: str, qual: str) -> str | None:
    cells = row.cells.get(family, {}).get(qual.encode())
    return cells[0].value.decode() if cells else None


def get_balance(account_id: str) -> dict | None:
    """Point read: single row lookup by account_id."""
    try:
        row = _table("account_balance").read_row(account_id.encode())
        if row is None:
            return None
        return {
            "account_id": account_id,
            "currency": _cell(row, "bal", "currency") or "USD",
            "balance": float(_cell(row, "bal", "balance") or 0),
            "last_activity_at": _cell(row, "bal", "as_of"),
        }
    except Exception:
        return None  # caller falls back to BigQuery


def get_transactions(account_id: str, limit: int = 50) -> list[dict]:
    """Prefix scan: newest-first by construction of the reverse-ts row key."""
    try:
        from google.cloud.bigtable.row_set import RowSet
        rs = RowSet()
        rs.add_row_range_with_prefix(f"{account_id}#")
        out = []
        for row in _table("txn_by_account").read_rows(row_set=rs, limit=limit):
            out.append({
                "transaction_id": _cell(row, "txn", "transaction_id") or row.row_key.decode().split("#")[-1],
                "txn_type": _cell(row, "txn", "txn_type") or "",
                "amount": float(_cell(row, "txn", "amount") or 0),
                "currency": _cell(row, "txn", "currency") or "USD",
                "status": _cell(row, "txn", "status") or "",
                "event_time": _cell(row, "txn", "event_time") or "",
            })
        return out
    except Exception:
        return []  # caller falls back to BigQuery
