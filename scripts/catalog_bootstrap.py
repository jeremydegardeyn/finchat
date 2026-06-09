#!/usr/bin/env python3
"""
Knowledge Catalog bootstrap (docs/12, ADR-0011) — run AFTER `terraform apply`
with enable_catalog=true (which creates the aspect types, entry groups + datascans).

For the 5 built FinChat data products it:
  1. Creates a Business Glossary + terms (the business concepts agents search by).
  2. Attaches **aspects** to each BigQuery entry:
       - data-product   (ownership, criticality, certification, SLA, cost center)
       - governance     (PII classification)
       - data-contract  (version, status, SLAs, guarantees, contract_ref)
  3. Publishes **insights** (latest data-profile / data-quality scan results) onto
     each entry's operational aspect.

Run the scans first:  ./scripts/run_datascans.sh <env>
Usage (repo root):     python scripts/catalog_bootstrap.py [dev|test|prod]
"""
from __future__ import annotations

import shutil
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("scripts", 1)[0] + "scripts")
from products_catalog import PROJECT, REGION, fqn, products  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252.
except Exception:
    pass

ENV = sys.argv[1] if len(sys.argv) > 1 else "dev"
PREFIX = f"finchat-{ENV}"
GCLOUD = shutil.which("gcloud") or "gcloud"
PRODUCTS = products(ENV)

GLOSSARY_TERMS = {
    "authoritative-customer-record": "The single source of truth for a customer (Customer Master).",
    "customer-demographics": "Customer attributes and segmentation.",
    "deposit-transaction": "A posted deposit/withdrawal/transfer/fee on a deposit account.",
    "overdraft-history": "Record of negative-balance events used in risk decisions.",
    "credit-exposure": "Outstanding loan amount and risk for a customer.",
    "fraud-transaction-history": "Transactions flagged or reviewed for fraud (enterprise target).",
}


def project_number() -> str:
    return subprocess.check_output(
        [GCLOUD, "projects", "describe", PROJECT, "--format=value(projectNumber)"],
        text=True).strip()


def create_glossary():
    gid = f"{PREFIX}-banking"
    subprocess.run([GCLOUD, "dataplex", "glossaries", "create", gid,
                    f"--project={PROJECT}", f"--location={REGION}",
                    "--display-name=FinChat Banking Glossary"], check=False)
    parent = f"projects/{PROJECT}/locations/{REGION}/glossaries/{gid}"
    for term, desc in GLOSSARY_TERMS.items():
        subprocess.run([GCLOUD, "dataplex", "glossaries", "terms", "create", term,
                        f"--glossary={gid}", f"--location={REGION}", f"--project={PROJECT}",
                        f"--parent={parent}", f"--display-name={term}",
                        f"--description={desc}"], check=False)
    print(f"glossary {gid}: {len(GLOSSARY_TERMS)} terms (409 'already exists' is OK)")


def _find_entry(client, scope, table_fqn):
    """Locate a BigQuery catalog entry by table name, matching on FQN; fall back
    to lookup-by-FQN. Returns the full entry (view=ALL) or None."""
    table = table_fqn.split(".")[-1]
    try:
        for res in client.search_entries(request={
                "name": scope, "query": table, "page_size": 10}):
            de = getattr(res, "dataplex_entry", None)
            efqn = (getattr(de, "fully_qualified_name", "") or res.linked_resource or "")
            if de and de.name and (table_fqn in efqn or efqn.endswith(table)):
                return client.get_entry(request={"name": de.name, "view": "ALL"})
    except Exception:
        pass
    try:
        return client.lookup_entry(request={
            "name": scope, "fully_qualified_name": table_fqn, "view": "ALL"})
    except Exception:
        return None


def attach_aspects(num: str):
    try:
        from google.cloud import dataplex_v1
        from google.protobuf import struct_pb2
    except ImportError:
        print("  ✗ run: pip install -U google-cloud-dataplex")
        return
    if not hasattr(dataplex_v1, "CatalogServiceClient"):
        print("  ✗ google-cloud-dataplex too old (need CatalogServiceClient): pip install -U google-cloud-dataplex")
        return
    client = dataplex_v1.CatalogServiceClient()
    scope = f"projects/{PROJECT}/locations/global"

    def key(kind):  # aspect types are GLOBAL (see catalog module)
        return f"{num}.global.{PREFIX}-{kind}"

    def atype(kind):
        return f"projects/{PROJECT}/locations/global/aspectTypes/{PREFIX}-{kind}"

    for p in PRODUCTS:
        try:
            entry = _find_entry(client, scope, fqn(p))
            if entry is None:
                print(f"  ✗ {p['display']:24s} entry not found ({fqn(p)})")
                continue
            c = p["contract"]
            dp = struct_pb2.Struct(); dp.update({
                "business_domain": p["domain"], "product_owner": p["owner"],
                "steward": p["steward"], "criticality": p["criticality"],
                "certification_status": p["certification"], "sla": p["sla"],
                "cost_center": p["cost_center"]})
            gov = struct_pb2.Struct(); gov.update({"pii_classification": p["pii"]})
            dc = struct_pb2.Struct(); dc.update({
                "contract_version": c["version"],
                "status": "CANDIDATE" if p["certification"] == "CANDIDATE" else "ACTIVE",
                "freshness_sla": c["freshness"], "availability_sla": c["availability"],
                "guarantees": c["guarantees"], "deprecation_policy": c["deprecation_policy"],
                "contract_ref": f"contracts/{p['id']}.yaml"})
            entry.aspects[key("data-product")] = dataplex_v1.Aspect(aspect_type=atype("data-product"), data=dp)
            entry.aspects[key("governance")] = dataplex_v1.Aspect(aspect_type=atype("governance"), data=gov)
            entry.aspects[key("data-contract")] = dataplex_v1.Aspect(aspect_type=atype("data-contract"), data=dc)
            client.update_entry(request={
                "entry": entry, "update_mask": {"paths": ["aspects"]},
                "aspect_keys": [key("data-product"), key("governance"), key("data-contract")]})
            print(f"  ✓ {p['display']:24s} -> {fqn(p)}  [data-product, governance, data-contract]")
        except Exception as e:
            print(f"  ✗ {p['display']:24s} {type(e).__name__}: {e}")


def _latest_job(scan_id: str):
    """Newest SUCCEEDED job for a scan -> the full job proto (or None)."""
    from google.cloud import dataplex_v1
    ds = dataplex_v1.DataScanServiceClient()
    parent = f"projects/{PROJECT}/locations/{REGION}/dataScans/{scan_id}"
    try:
        for j in ds.list_data_scan_jobs(request={"parent": parent}):  # newest first
            job = ds.get_data_scan_job(request={"name": j.name, "view": "FULL"})
            if job.state == dataplex_v1.DataScanJob.State.SUCCEEDED:
                return job
    except Exception:
        return None
    return None


def publish_insights(num: str):
    """Publish each product's latest scan result to its operational aspect.
    Transaction uses its detailed quality scan (score + pass/fail); the rest use
    their profile scan (row count)."""
    try:
        from google.cloud import dataplex_v1
        from google.protobuf import struct_pb2
    except ImportError:
        print("  ✗ run: pip install -U google-cloud-dataplex"); return
    if not hasattr(dataplex_v1, "DataScanServiceClient"):
        print("  ✗ google-cloud-dataplex too old for DataScan API."); return
    client = dataplex_v1.CatalogServiceClient()
    scope = f"projects/{PROJECT}/locations/global"
    op_key = f"{num}.global.{PREFIX}-operational"
    op_type = f"projects/{PROJECT}/locations/global/aspectTypes/{PREFIX}-operational"

    for p in PRODUCTS:
        try:
            score_str, when = None, None
            if p["id"] == "deposit-transactions":
                job = _latest_job(f"{PREFIX}-silver-txn-quality")
                if job:
                    r = job.data_quality_result
                    score_str = f"{float(r.score):.1f}% ({'PASS' if r.passed else 'FAIL'})"
                    when = job.end_time
            if score_str is None:
                job = _latest_job(f"{PREFIX}-{p['id']}-profile")
                if job:
                    score_str = f"profiled: {int(job.data_profile_result.row_count)} rows"
                    when = job.end_time
            if score_str is None:
                print(f"  ✗ {p['display']:24s} no SUCCEEDED scan job yet (run ./scripts/run_datascans.sh {ENV})")
                continue
            entry = _find_entry(client, scope, fqn(p))
            if entry is None:
                print(f"  ✗ {p['display']:24s} entry not found"); continue
            last_run = when.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(when, "strftime") else str(when)
            data = struct_pb2.Struct(); data.update({
                "data_quality_score": score_str, "last_dq_run": last_run,
                "freshness_sla": p["contract"]["freshness"], "pipeline_version": "dataflow-flex"})
            entry.aspects[op_key] = dataplex_v1.Aspect(aspect_type=op_type, data=data)
            client.update_entry(request={
                "entry": entry, "update_mask": {"paths": ["aspects"]},
                "aspect_keys": [op_key]})
            print(f"  ✓ {p['display']:24s} -> {score_str} @ {last_run}")
        except Exception as e:
            print(f"  ✗ {p['display']:24s} {type(e).__name__}: {e}")


def enrich_data_products(num: str):
    """Attach the FinChat aspects (+ a rich overview) to each **data-product
    entry** (entryGroup @dataplex, regional) so they show on the Data Products
    page's *Aspects* tab. Run data_products.py first (creates the entries).

    The page's *Contract* and *Insights→Query-recommendations* tabs use Google's
    gated system aspect types ('contract', 'query-recommendations') that aren't
    usable via the public API — add those via the console '+ Add'/'Edit' (the
    same guarantees live in the data-contract aspect + contracts/<id>.yaml)."""
    try:
        from google.cloud import dataplex_v1
        from google.protobuf import struct_pb2
        from google.api_core.exceptions import NotFound
    except ImportError:
        print("  ✗ run: pip install -U google-cloud-dataplex"); return
    client = dataplex_v1.CatalogServiceClient()

    def key(k):
        return f"{num}.global.{PREFIX}-{k}"

    def at(k):
        return f"projects/{PROJECT}/locations/global/aspectTypes/{PREFIX}-{k}"

    def aspect(atype, fields):
        s = struct_pb2.Struct(); s.update(fields)
        return dataplex_v1.Aspect(aspect_type=atype, data=s)

    for p in PRODUCTS:
        dp_id = f"finchat-{ENV}-{p['id']}"
        name = (f"projects/{num}/locations/{REGION}/entryGroups/@dataplex/entries/"
                f"projects/{num}/locations/{REGION}/dataProducts/{dp_id}")
        try:
            entry = client.get_entry(request={"name": name, "view": "ALL"})
        except NotFound:
            print(f"  ✗ {p['display']:24s} data-product entry not found (run data_products.py first)")
            continue
        except Exception as e:
            print(f"  ✗ {p['display']:24s} {type(e).__name__}: {e}"); continue
        c = p["contract"]
        asp = {
            key("data-product"): aspect(at("data-product"), {
                "business_domain": p["domain"], "product_owner": p["owner"],
                "steward": p["steward"], "criticality": p["criticality"],
                "certification_status": p["certification"], "sla": p["sla"],
                "cost_center": p["cost_center"]}),
            key("governance"): aspect(at("governance"), {"pii_classification": p["pii"]}),
            key("data-contract"): aspect(at("data-contract"), {
                "contract_version": c["version"],
                "status": "CANDIDATE" if p["certification"] == "CANDIDATE" else "ACTIVE",
                "freshness_sla": c["freshness"], "availability_sla": c["availability"],
                "guarantees": c["guarantees"], "deprecation_policy": c["deprecation_policy"],
                "contract_ref": f"contracts/{p['id']}.yaml"}),
        }
        # Insight (latest scan) -> operational aspect.
        job = (_latest_job(f"{PREFIX}-silver-txn-quality") if p["id"] == "deposit-transactions"
               else None) or _latest_job(f"{PREFIX}-{p['id']}-profile")
        if job is not None:
            if p["id"] == "deposit-transactions" and job.data_quality_result.score is not None:
                r = job.data_quality_result
                score = f"{float(r.score):.1f}% ({'PASS' if r.passed else 'FAIL'})"
            else:
                score = f"profiled: {int(job.data_profile_result.row_count)} rows"
            asp[key("operational")] = aspect(at("operational"), {
                "data_quality_score": score,
                "last_dq_run": job.end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "freshness_sla": c["freshness"], "pipeline_version": "dataflow-flex"})
        # Rich overview (system aspect, world-usable) -> product Overview.
        overview = (f"<p><b>{p['display']}</b> — {p['description']}</p>"
                    f"<p>Contract <b>v{c['version']}</b> ({'CANDIDATE' if p['certification']=='CANDIDATE' else 'ACTIVE'})"
                    f" · freshness {c['freshness']} · availability {c['availability']}"
                    f" · owner {p['owner']}</p>"
                    f"<p>Guarantees: {c['guarantees']}</p>"
                    f"<p>Contract as code: contracts/{p['id']}.yaml</p>")
        asp["655216118709.global.overview"] = aspect(
            "projects/dataplex-types/locations/global/aspectTypes/overview",
            {"content": overview, "contentType": "HTML"})

        for k, a in asp.items():
            entry.aspects[k] = a
        try:
            client.update_entry(request={
                "entry": entry, "update_mask": {"paths": ["aspects"]},
                "aspect_keys": list(asp.keys())})
            print(f"  ✓ {p['display']:24s} aspects+overview on data-product entry")
        except Exception as e:
            print(f"  ✗ {p['display']:24s} {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"== Catalog bootstrap ({ENV}) ==")
    num = project_number()
    print("project number:", num)
    print("-- glossary --"); create_glossary()
    print("-- attach aspects to BQ table entries (catalog search) --"); attach_aspects(num)
    print("-- publish insights to operational aspect (table entries) --"); publish_insights(num)
    print("-- enrich DATA PRODUCT entries (Data Products page Aspects tab) --"); enrich_data_products(num)
    print("Done. Search: gcloud dataplex entries search 'deposit transaction' --project", PROJECT)
