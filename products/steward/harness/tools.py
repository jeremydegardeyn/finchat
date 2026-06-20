"""Steward tools — the durable agent's hands.

Real data-quality / reconciliation checks against the FinChat gold + silver tables.
Each check reads BigQuery metadata (`__TABLES__`) for row count + freshness, falling
back to a `COUNT(*)` for views — cheap (no full scans for base tables) and uses only
the run SA's existing `bigquery.dataViewer` + `bigquery.jobUser`.

Runs fully OFFLINE (no GCP_PROJECT or BigQuery client) by returning a healthy stub, so
the harness, demo, and tests work without GCP — same discipline as the loan agent.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

GCP_PROJECT = os.getenv("GCP_PROJECT", "")
SILVER = os.getenv("SILVER_DATASET", "")
GOLD = os.getenv("GOLD_DATASET", "")
LOANS = os.getenv("LOANS_DATASET", "")
GRAPH = os.getenv("GRAPH_DATASET", "")
KB = os.getenv("KB_DATASET", "")
FRESHNESS_MAX_HOURS = float(os.getenv("FRESHNESS_MAX_HOURS", "26"))  # nightly tolerance


def checks() -> list[tuple[str, str, str, bool]]:
    """Reconciliation targets: (product_id, dataset, table, expect_fresh).

    Mirrors the data products in scripts/products_catalog.py; kept local so the
    container stays self-contained.
    """
    out: list[tuple[str, str, str, bool]] = []
    if SILVER:
        out += [("deposit-transactions", SILVER, "transaction", True),
                ("customer-master", SILVER, "customer", False)]
    if GOLD:
        out += [("overdraft-history", GOLD, "overdraft_history", False)]
    if LOANS:
        out += [("loan-master", LOANS, "loan_status", False)]  # serving view
    if KB:
        out += [("bank-knowledge-base", KB, "kb_chunks", False)]
    if GRAPH:
        out += [("customer-360-analytics", GRAPH, "customer_360", False)]  # view
    if not out:  # offline / unconfigured — give the planner a representative set
        out = [("deposit-transactions", "finchat_silver", "transaction", True),
               ("customer-master", "finchat_silver", "customer", False),
               ("overdraft-history", "finchat_gold", "overdraft_history", False),
               ("loan-master", "finchat_loans", "loan_status", False)]
    return out


def _bq_available() -> bool:
    if not GCP_PROJECT:
        return False
    try:
        import google.cloud.bigquery  # noqa: F401
        return True
    except ImportError:
        return False


def run_dq_check(dataset: str, table: str, expect_fresh: bool = False) -> dict:
    """Run a real data-quality check and return {target, ok, row_count, detail}."""
    target = f"{dataset}.{table}"
    if not _bq_available():
        return {"target": target, "ok": True, "row_count": None,
                "detail": f"(offline stub) {target}: assumed healthy"}

    from google.cloud import bigquery
    client = bigquery.Client(project=GCP_PROJECT)
    try:
        meta = list(client.query(
            f"SELECT row_count, type, last_modified_time "
            f"FROM `{GCP_PROJECT}.{dataset}.__TABLES__` WHERE table_id = @t",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("t", "STRING", table)])).result())

        if meta and meta[0]["type"] == 1:  # base table — use cheap metadata
            row_count = meta[0]["row_count"]
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            age_h = (now_ms - meta[0]["last_modified_time"]) / 3.6e6
        else:  # view / not in __TABLES__ — count rows
            row_count = list(client.query(
                f"SELECT COUNT(*) AS n FROM `{GCP_PROJECT}.{dataset}.{table}`"
            ).result())[0]["n"]
            age_h = None

        issues = []
        if row_count is not None and row_count == 0:
            issues.append("EMPTY (contract violation)")
        if expect_fresh and age_h is not None and age_h > FRESHNESS_MAX_HOURS:
            issues.append(f"STALE ({age_h:.0f}h > {FRESHNESS_MAX_HOURS:.0f}h)")

        detail = f"{row_count if row_count is not None else '?'} rows"
        if age_h is not None:
            detail += f", {age_h:.0f}h old"
        if issues:
            detail += " — " + ", ".join(issues)
        return {"target": target, "ok": not issues, "row_count": row_count, "detail": detail}

    except Exception as e:  # missing table / permission / etc. — flag it
        return {"target": target, "ok": False, "row_count": None,
                "detail": f"check FAILED: {type(e).__name__}: {e}"}
