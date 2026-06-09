#!/usr/bin/env python3
"""
Dataplex **Data Products** bootstrap (the console "Data products" page, docs/12).

A Data Product is a first-class Dataplex resource (distinct from the catalog
*aspects* attached by catalog_bootstrap.py): a curated, owned, consumable package
that bundles BigQuery tables as *data assets*, with **access groups** consumers
can request access to (approval-gated) and per-asset IAM. Served by the preview
Data Products REST API (`dataplex.googleapis.com/v1/.../dataProducts`), which has
no gcloud command group or Terraform resource yet — so we drive it here.

For each of the 5 FinChat products this is idempotent and ensures:
  1. the data product (displayName, owner, description, labels);
  2. the BigQuery table as a data asset;
  3. access groups (consumer personas -> Google group) + approver emails;
  4. per-asset IAM roles granted to each access group on approval.

Usage (repo root):  python scripts/data_products.py [dev|test|prod]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, __file__.rsplit("scripts", 1)[0] + "scripts")
from products_catalog import PROJECT, REGION, products  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252.
except Exception:
    pass

ENV = sys.argv[1] if len(sys.argv) > 1 else "dev"
BASE = f"https://dataplex.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}"
# Binding per-asset IAM requires the access-group principals to be REAL Cloud
# Identity groups. Off by default (placeholder groups don't resolve); enable in a
# real org: FINCHAT_BIND_ASSET_IAM=1 python scripts/data_products.py <env>
BIND_ASSET_IAM = os.getenv("FINCHAT_BIND_ASSET_IAM", "").lower() in ("1", "true", "yes")


def _token() -> str:
    gcloud = shutil.which("gcloud") or "gcloud"
    return subprocess.check_output([gcloud, "auth", "print-access-token"], text=True).strip()


def _api(method: str, path: str, token: str, body: dict | None = None, _tries: int = 5) -> dict:
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 429 and _tries > 1:  # Data-Product per-minute quota — back off.
            ra = e.headers.get("Retry-After")
            time.sleep(float(ra) if (ra and ra.isdigit()) else 12)
            return _api(method, path, token, body, _tries - 1)
        return {"_error": e.code, "_body": e.read().decode(errors="replace")}


def _dataset_location(dataset: str, token: str) -> str:
    r = _api("GET", f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}"
             f"/datasets/{dataset}", token)
    return (r.get("location") or "").lower()


def _wait(op: dict, token: str, label: str) -> bool:
    """Poll an LRO to completion. Returns True on success (or already-exists)."""
    if "_error" in op:
        body = op.get("_body", "")
        if op["_error"] == 409 or "ALREADY_EXISTS" in body:
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
                print(f"  ✗ {label}: {st['error'].get('message', '')[:200]}")
                return False
            return True
    print(f"  ~ {label}: LRO not done after 60s (continuing)")
    return True


def main():
    token = _token()
    print(f"== Data Products bootstrap ({ENV}) ==")
    for p in products(ENV):
        full_id = f"finchat-{ENV}-{p['id']}"
        name, dataset, table = p["display"], p["dataset"], p["table"]

        # 1. product ---------------------------------------------------------
        op = _api("POST", f"/dataProducts?dataProductId={full_id}", token, {
            "displayName": name, "description": p["description"],
            "ownerEmails": [p["owner"]],
            "labels": {"env": ENV, "domain": p["domain"], "criticality": p["criticality"].lower()}})
        if not _wait(op, token, f"product {name}"):
            continue

        # 2. access groups + approver emails (consumers may request access) --
        groups = {g["id"]: {"id": g["id"], "displayName": g["display"],
                            "description": g["desc"], "principal": {"googleGroup": g["group"]}}
                  for g in p["access_groups"]}
        gop = _api("PATCH",
                   f"/dataProducts/{full_id}?updateMask=accessGroups,accessApprovalConfig",
                   token, {"accessGroups": groups,
                           "accessApprovalConfig": {"approverEmails": [p["owner"]]}})
        _wait(gop, token, f"  access groups {name}")

        # 3. data asset (co-located BQ table) --------------------------------
        loc = _dataset_location(dataset, token)
        if loc and loc != REGION:
            print(f"  ! {name:24s} asset skipped: {dataset} is in '{loc}' "
                  f"(product is '{REGION}'); co-locate to attach")
            continue
        asset_id = table.replace("_", "-")  # asset ids must match ^[a-z][a-z0-9-]*$
        resource = (f"//bigquery.googleapis.com/projects/{PROJECT}"
                    f"/datasets/{dataset}/tables/{table}")
        aop = _api("POST", f"/dataProducts/{full_id}/dataAssets?dataAssetId={asset_id}",
                   token, {"resource": resource})
        if not _wait(aop, token, f"  asset {table}"):
            continue

        # 4. per-asset IAM granted to each access group (on approval) --------
        #    Needs real Cloud Identity groups; opt-in via FINCHAT_BIND_ASSET_IAM.
        iam_note = "IAM binding deferred (set FINCHAT_BIND_ASSET_IAM=1 with real groups)"
        if BIND_ASSET_IAM:
            cfgs = {g["id"]: {"iamRoles": g["roles"]} for g in p["access_groups"]}
            cop = _api("PATCH",
                       f"/dataProducts/{full_id}/dataAssets/{asset_id}?updateMask=accessGroupConfigs",
                       token, {"accessGroupConfigs": cfgs})
            iam_note = "IAM bound" if _wait(cop, token, f"  asset IAM {table}") else "IAM bind failed"

        gnames = ", ".join(g["id"] for g in p["access_groups"])
        print(f"  ✓ {name:24s} -> {dataset}.{table}  [groups: {gnames}; {iam_note}]")

    print("Done. View: https://console.cloud.google.com/dataplex/catalog/data-products"
          f"?project={PROJECT}")


if __name__ == "__main__":
    main()
