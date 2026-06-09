#!/usr/bin/env python3
"""
Dataplex **Data Products** bootstrap (the console "Data products" page, docs/12).

A Data Product is a first-class Dataplex resource (distinct from the catalog
*aspects* attached by catalog_bootstrap.py): a curated, owned, consumable package
that bundles one or more BigQuery tables as *data assets*. It is served by the
preview Data Products REST API (`dataplex.googleapis.com/v1/.../dataProducts`),
which has no gcloud command group or Terraform resource yet — so we drive it here.

Creates the 5 FinChat data products + their BQ assets, idempotently (skips ones
that already exist; polls each create's long-running operation to completion).

Usage (repo root):  python scripts/data_products.py [dev|test|prod]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252.
except Exception:
    pass

PROJECT = "strongsville-city-schools"
REGION = "us-central1"
ENV = sys.argv[1] if len(sys.argv) > 1 else "dev"
BASE = f"https://dataplex.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}"

# id -> (displayName, owner, description, dataset, table, labels)
PRODUCTS = [
    ("deposit-transactions", "Deposit Transactions", "deposits-product@datadinosaur.com",
     "Posted deposit/withdrawal/transfer/fee events on deposit accounts (silver).",
     f"finchat_silver_{ENV}", "transaction", {"domain": "deposits", "criticality": "high"}),
    ("customer-master", "Customer Master", "customer-product@datadinosaur.com",
     "Authoritative single source of truth for customer identity & demographics (silver).",
     f"finchat_silver_{ENV}", "customer", {"domain": "customer", "criticality": "critical"}),
    ("overdraft-history", "Overdraft History", "risk-product@datadinosaur.com",
     "Negative-balance events used in risk decisioning (gold).",
     f"finchat_gold_{ENV}", "overdraft_history", {"domain": "risk", "criticality": "high"}),
    ("loan-master", "Loan Master", "lending-product@datadinosaur.com",
     "Loan applications, risk scores and approval status (lending).",
     f"finchat_loans_{ENV}", "loan_status", {"domain": "lending", "criticality": "high"}),
    ("bank-knowledge-base", "Bank Knowledge Base", "ai-platform@datadinosaur.com",
     "Embedded policy/product documents grounding the FinChat agent (RAG corpus).",
     f"finchat_kb_{ENV}", "kb_chunks", {"domain": "marketing", "criticality": "medium"}),
]


def _token() -> str:
    gcloud = "gcloud"  # resolved on PATH; on Windows the shell maps to gcloud.cmd
    import shutil
    gcloud = shutil.which("gcloud") or gcloud
    return subprocess.check_output([gcloud, "auth", "print-access-token"], text=True).strip()


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode(errors="replace")}


def _dataset_location(dataset: str, token: str) -> str:
    r = _api("GET", f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}"
             f"/datasets/{dataset}", token)
    return (r.get("location") or "").lower()


def _wait(op: dict, token: str, label: str) -> bool:
    """Poll an LRO to completion. Returns True on success."""
    if "_error" in op:
        body = op.get("_body", "")
        if op["_error"] == 409 or "ALREADY_EXISTS" in body:
            print(f"  = {label} already exists")
            return True
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        print(f"  ✗ {label}: HTTP {op['_error']} {' '.join(msg.split())[:200]}")
        return False
    name = op.get("name", "")
    if not name or op.get("done"):
        return True
    for _ in range(30):
        time.sleep(2)
        st = _api("GET", f"https://dataplex.googleapis.com/v1/{name}", token)
        if st.get("done"):
            if "error" in st:
                print(f"  ✗ {label}: {st['error'].get('message', '')[:160]}")
                return False
            return True
    print(f"  ~ {label}: still running (LRO not done after 60s)")
    return True


def main():
    token = _token()
    print(f"== Data Products bootstrap ({ENV}) ==")
    for pid, name, owner, desc, dataset, table, labels in PRODUCTS:
        full_id = f"finchat-{ENV}-{pid}"
        lab = {"env": ENV, **labels}
        op = _api("POST", f"/dataProducts?dataProductId={full_id}", token, {
            "displayName": name, "description": desc, "ownerEmails": [owner], "labels": lab})
        if not _wait(op, token, f"product {name}"):
            continue
        # A regional data product can only bundle a co-located BQ table. Skip
        # (with a clear note) if the dataset isn't in REGION — e.g. a dataset
        # created by raw DDL defaults to the US multi-region.
        loc = _dataset_location(dataset, token)
        if loc and loc != REGION:
            print(f"  ! {name:24s} asset skipped: {dataset} is in '{loc}', "
                  f"product is in '{REGION}' (co-locate the dataset to attach it)")
            continue
        # Attach the BigQuery table as a data asset (idempotent).
        resource = (f"//bigquery.googleapis.com/projects/{PROJECT}"
                    f"/datasets/{dataset}/tables/{table}")
        asset_id = table.replace("_", "-")  # asset ids must match ^[a-z][a-z0-9-]*$
        aop = _api("POST", f"/dataProducts/{full_id}/dataAssets?dataAssetId={asset_id}",
                   token, {"resource": resource})
        ok = _wait(aop, token, f"  asset {table}")
        if ok:
            print(f"  ✓ {name:24s} -> {dataset}.{table}")
    print("Done. View: https://console.cloud.google.com/dataplex/catalog/data-products"
          f"?project={PROJECT}")


if __name__ == "__main__":
    main()
