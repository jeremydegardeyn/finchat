"""Steward tools — the durable agent's hands.

The steward does NOT re-implement data-quality checks (Dataplex Auto DQ already does
that, Inc 10). It sits ON TOP of the existing Dataplex DQ datascans: it reads their
results, and for each FAILED rule drives a remediation-with-approval loop.

Governance posture: the steward never directly rewrites production financial tables.
It records an approved remediation *order* and re-runs the scan to verify — the owning
team executes the actual fix. This is the reasoning/orchestration/HITL work that
scheduled SQL can't do; the checking stays in Dataplex.

Runs OFFLINE (no GCP_PROJECT / no dataplex client) via a representative stub finding,
so the harness, demo, and tests work without GCP.
"""
from __future__ import annotations

import os

PROJECT = os.getenv("GCP_PROJECT", "")
REGION = os.getenv("GOOGLE_CLOUD_LOCATION", os.getenv("DATAPLEX_REGION", "us-central1"))
ENV = os.getenv("ENV", "dev")
PREFIX = f"finchat-{ENV}"
# DQ datascans that carry pass/fail rules (profile scans don't). Inc 10 ships one.
DQ_SCANS = [s.strip() for s in os.getenv("DQ_SCANS", "silver-txn-quality").split(",") if s.strip()]


def _dataplex_available() -> bool:
    if not PROJECT:
        return False
    try:
        import google.cloud.dataplex_v1  # noqa: F401
        return True
    except ImportError:
        return False


def _latest_job(ds, scan_id: str):
    """Newest SUCCEEDED job for a scan -> full job proto (or None). Mirrors
    scripts/catalog_bootstrap.py::_latest_job."""
    from google.cloud import dataplex_v1
    parent = f"projects/{PROJECT}/locations/{REGION}/dataScans/{scan_id}"
    for j in ds.list_data_scan_jobs(request={"parent": parent}):  # newest first
        job = ds.get_data_scan_job(request={"name": j.name, "view": "FULL"})
        if job.state == dataplex_v1.DataScanJob.State.SUCCEEDED:
            return job
    return None


def _stub_finding() -> dict:
    return {"id": f"{PREFIX}-silver-txn-quality#0", "scan": f"{PREFIX}-silver-txn-quality",
            "label": "Remediate silver-txn-quality: VALIDITY on amount failed",
            "dimension": "VALIDITY", "column": "amount",
            "evaluated": 1000, "passed_count": 987,
            "failing_rows_query": "SELECT * FROM `…silver.transaction` WHERE amount IS NULL"}


def read_findings() -> list[dict]:
    """Read the latest Dataplex DQ scan results; return one finding per FAILED rule.
    Empty list = all rules passed (nothing to remediate)."""
    if not _dataplex_available():
        return [_stub_finding()]

    from google.cloud import dataplex_v1
    ds = dataplex_v1.DataScanServiceClient()
    findings: list[dict] = []
    for scan in DQ_SCANS:
        scan_id = f"{PREFIX}-{scan}"
        try:
            job = _latest_job(ds, scan_id)
        except Exception:
            continue
        if not job or not job.data_quality_result:
            continue
        for idx, rr in enumerate(job.data_quality_result.rules):
            if rr.passed:
                continue
            rule = rr.rule
            col = getattr(rule, "column", "") or ""
            dim = getattr(rule, "dimension", "") or ""
            findings.append({
                "id": f"{scan_id}#{idx}", "scan": scan_id,
                "label": f"Remediate {scan}: {dim or 'rule'} on {col or 'table'} failed",
                "dimension": dim, "column": col,
                "evaluated": int(getattr(rr, "evaluated_count", 0) or 0),
                "passed_count": int(getattr(rr, "passed_count", 0) or 0),
                "failing_rows_query": getattr(rr, "failing_rows_query", "") or "",
            })
    return findings


def apply_remediation(finding: dict, proposal: str, decision: dict) -> str:
    """Exactly-once side effect after approval: record the approved remediation ORDER
    and re-run the scan to verify. Does NOT mutate financial tables (the owning team
    executes the fix; the re-scan confirms it)."""
    approver = decision.get("approver", "")
    note = decision.get("note", "")
    rerun = "(offline) scan re-run skipped"
    if _dataplex_available():
        try:
            from google.cloud import dataplex_v1
            ds = dataplex_v1.DataScanServiceClient()
            ds.run_data_scan(request={
                "name": f"projects/{PROJECT}/locations/{REGION}/dataScans/{finding['scan']}"})
            rerun = f"re-ran {finding['scan']} to verify"
        except Exception as e:
            rerun = f"scan re-run failed: {type(e).__name__}"
    out = f"Remediation ORDER approved by {approver or 'approver'}: {proposal[:160]} | {rerun}"
    return out + (f" | note: {note}" if note else "")
