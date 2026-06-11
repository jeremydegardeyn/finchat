#!/usr/bin/env python3
"""
Backfill the Bigtable hot path from BigQuery (ADR-0017).

Loads silver transactions into `txn_by_account` (row key account_id#reverse_ts#txn8,
newest-first prefix scans) and current balances into `account_balance` (point reads).
In the enterprise tier the Beam pipeline dual-writes this continuously; the backfill
seeds it (and is rerunnable — Bigtable mutations are idempotent by row key).

Near-zero-cost dev: run against the local emulator —
    gcloud emulators bigtable start --host-port=localhost:8086
    BIGTABLE_EMULATOR_HOST=localhost:8086 python scripts/bigtable_backfill.py dev
Against a real instance (enable_bigtable=true): omit BIGTABLE_EMULATOR_HOST.

Usage: python scripts/bigtable_backfill.py [dev|test|prod] [--instance finchat-<env>-hot] [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = "strongsville-city-schools"
REVERSE_EPOCH = 9_999_999_999


def reverse_ts(epoch_seconds: int) -> str:
    return str(REVERSE_EPOCH - int(epoch_seconds)).zfill(10)


def ensure_tables(instance, emulated: bool):
    """On the emulator (or a fresh instance) create tables + families if missing."""
    from google.cloud.bigtable import column_family
    specs = {"txn_by_account": "txn", "account_balance": "bal"}
    for name, fam in specs.items():
        table = instance.table(name)
        try:
            if not table.exists():
                table.create(column_families={fam: column_family.MaxVersionsGCRule(1)})
                print(f"  created table {name} (cf={fam})")
        except Exception as e:
            print(f"  table {name}: {type(e).__name__}: {e}")
    return [instance.table(n) for n in specs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("env", nargs="?", default="dev")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--limit", type=int, default=50000)
    args = ap.parse_args()
    instance_id = args.instance or f"finchat-{args.env}-hot"

    import os
    emulated = bool(os.getenv("BIGTABLE_EMULATOR_HOST"))
    print(f"== Bigtable backfill ({args.env}) -> instance {instance_id} "
          f"{'[EMULATOR]' if emulated else '[LIVE]'} ==")

    from google.cloud import bigquery, bigtable
    bt_client = bigtable.Client(project=PROJECT, admin=True)
    instance = bt_client.instance(instance_id)
    txn_tbl, bal_tbl = ensure_tables(instance, emulated)

    bq = bigquery.Client(project=PROJECT)

    # --- transactions -> txn_by_account -------------------------------------
    sql = f"""
      SELECT transaction_id, account_id, txn_type, CAST(amount AS STRING) amount,
             currency, status, CAST(event_time AS STRING) event_time,
             UNIX_SECONDS(event_time) epoch
      FROM `{PROJECT}.finchat_silver_{args.env}.transaction`
      ORDER BY event_time DESC LIMIT {args.limit}"""
    rows, batch, n = bq.query(sql).result(), [], 0
    for r in rows:
        key = f"{r['account_id']}#{reverse_ts(r['epoch'])}#{r['transaction_id'][:8]}"
        m = txn_tbl.direct_row(key.encode())
        for q in ("transaction_id", "txn_type", "amount", "currency", "status", "event_time"):
            m.set_cell("txn", q, str(r[q]))
        batch.append(m); n += 1
        if len(batch) >= 500:
            txn_tbl.mutate_rows(batch); batch = []
    if batch:
        txn_tbl.mutate_rows(batch)
    print(f"  txn_by_account: {n} rows")

    # --- balances -> account_balance -----------------------------------------
    sql = f"""
      SELECT account_id, CAST(balance AS STRING) balance, currency,
             CAST(last_activity_at AS STRING) AS as_of
      FROM `{PROJECT}.finchat_gold_{args.env}.account_balance`"""
    batch, n = [], 0
    try:
        for r in bq.query(sql).result():
            m = bal_tbl.direct_row(str(r["account_id"]).encode())
            m.set_cell("bal", "balance", str(r["balance"]))
            m.set_cell("bal", "currency", str(r["currency"] or "USD"))
            m.set_cell("bal", "as_of", str(r["as_of"] or datetime.now(timezone.utc).isoformat()))
            batch.append(m); n += 1
            if len(batch) >= 500:
                bal_tbl.mutate_rows(batch); batch = []
        if batch:
            bal_tbl.mutate_rows(batch)
    except Exception as e:
        print(f"  account_balance source query failed ({type(e).__name__}) — check the gold view name")
    print(f"  account_balance: {n} rows")
    print("done.")


if __name__ == "__main__":
    main()
