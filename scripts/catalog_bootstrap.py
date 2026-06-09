#!/usr/bin/env python3
"""
Knowledge Catalog bootstrap (docs/12, ADR-0010) — run AFTER `terraform apply`
with enable_catalog=true (which creates the aspect types + domain entry groups).

Does two things for the 5 *built* FinChat data products:
  1. Creates a Business Glossary + terms (the business concepts agents search by).
  2. Attaches `data-product` + `governance` aspects to the BigQuery entries so the
     catalog (and the agent's discover_data_product tool) returns rich, governed metadata.

Usage (repo root):  python scripts/catalog_bootstrap.py [dev|test|prod]

NOTE: Business Glossary + aspect-attachment APIs are new; this targets the live
catalog and may need a minor tweak on first run depending on your provider/gcloud
versions. It is idempotent-ish (re-running updates aspects).
"""
from __future__ import annotations

import shutil
import subprocess
import sys

PROJECT = "strongsville-city-schools"
REGION = "us-central1"
ENV = sys.argv[1] if len(sys.argv) > 1 else "dev"
PREFIX = f"finchat-{ENV}"
# On Windows gcloud is gcloud.cmd; subprocess needs the resolved path (not a bare name).
GCLOUD = shutil.which("gcloud") or "gcloud"

# --- The 5 data products built today: BQ table -> business metadata ----------
PRODUCTS = [
    {"fqn": f"bigquery:{PROJECT}.finchat_silver_{ENV}.transaction",
     "product": "Deposit Transactions", "domain": "deposits", "criticality": "HIGH",
     "certification": "CERTIFIED", "owner": "deposits-product@datadinosaur.com",
     "steward": "data-steward@datadinosaur.com", "pii": "PII_FINANCIAL",
     "sla": "freshness<=15m; 99.9% avail", "cost_center": "CC-DEPOSITS"},
    {"fqn": f"bigquery:{PROJECT}.finchat_silver_{ENV}.customer",
     "product": "Customer Master", "domain": "customer", "criticality": "CRITICAL",
     "certification": "CERTIFIED", "owner": "customer-product@datadinosaur.com",
     "steward": "data-steward@datadinosaur.com", "pii": "PII_DIRECT",
     "sla": "daily", "cost_center": "CC-CUSTOMER"},
    {"fqn": f"bigquery:{PROJECT}.finchat_gold_{ENV}.overdraft_history",
     "product": "Overdraft History", "domain": "risk", "criticality": "HIGH",
     "certification": "CERTIFIED", "owner": "risk-product@datadinosaur.com",
     "steward": "data-steward@datadinosaur.com", "pii": "PII_FINANCIAL",
     "sla": "daily", "cost_center": "CC-RISK"},
    {"fqn": f"bigquery:{PROJECT}.finchat_loans_{ENV}.loan_status",
     "product": "Loan Master", "domain": "lending", "criticality": "HIGH",
     "certification": "CANDIDATE", "owner": "lending-product@datadinosaur.com",
     "steward": "data-steward@datadinosaur.com", "pii": "PII_FINANCIAL",
     "sla": "near-real-time", "cost_center": "CC-LENDING"},
    {"fqn": f"bigquery:{PROJECT}.finchat_kb_{ENV}.kb_chunks",
     "product": "Bank Knowledge Base", "domain": "marketing", "criticality": "MEDIUM",
     "certification": "CERTIFIED", "owner": "ai-platform@datadinosaur.com",
     "steward": "data-steward@datadinosaur.com", "pii": "PUBLIC",
     "sla": "on-publish", "cost_center": "CC-AI"},
]

# --- Glossary: the business concepts agents resolve to data products ----------
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
    """Best-effort glossary + terms via gcloud (API surface is new)."""
    gid = f"{PREFIX}-banking"
    subprocess.run([GCLOUD, "dataplex", "glossaries", "create", gid,
                    f"--project={PROJECT}", f"--location={REGION}",
                    "--display-name=FinChat Banking Glossary"], check=False)
    parent = f"projects/{PROJECT}/locations/{REGION}/glossaries/{gid}"
    for term, desc in GLOSSARY_TERMS.items():
        # gcloud needs the term-id positional AND --glossary/--location/--project to
        # resolve the term resource, AND --parent (the glossary) separately.
        subprocess.run([GCLOUD, "dataplex", "glossaries", "terms", "create", term,
                        f"--glossary={gid}", f"--location={REGION}", f"--project={PROJECT}",
                        f"--parent={parent}", f"--display-name={term}",
                        f"--description={desc}"], check=False)
    print(f"glossary {gid}: {len(GLOSSARY_TERMS)} terms (409 'already exists' is OK)")


def attach_aspects(num: str):
    try:
        from google.cloud import dataplex_v1
        from google.protobuf import struct_pb2
    except ImportError:
        print("  ✗ run: pip install -U google-cloud-dataplex")
        return
    if not hasattr(dataplex_v1, "CatalogServiceClient"):
        print("  ✗ your google-cloud-dataplex is too old for the Catalog API.")
        print("    run: pip install -U google-cloud-dataplex   (need CatalogServiceClient)")
        return
    client = dataplex_v1.CatalogServiceClient()
    # Aspect types are GLOBAL (see catalog module) so they're usable by BQ entries.
    dp_key = f"{num}.global.{PREFIX}-data-product"
    gov_key = f"{num}.global.{PREFIX}-governance"
    dp_type = f"projects/{PROJECT}/locations/global/aspectTypes/{PREFIX}-data-product"
    gov_type = f"projects/{PROJECT}/locations/global/aspectTypes/{PREFIX}-governance"

    scope = f"projects/{PROJECT}/locations/global"
    for p in PRODUCTS:
        try:
            # Find the BigQuery entry. Search by TABLE NAME (not the product display
            # name) and match on the fully-qualified name; fall back to lookup-by-FQN.
            table = p["fqn"].split(".")[-1]
            entry = None
            try:
                for res in client.search_entries(request={
                        "name": scope, "query": table, "page_size": 10}):
                    de = getattr(res, "dataplex_entry", None)
                    fqn = (getattr(de, "fully_qualified_name", "") or res.linked_resource or "")
                    if de and de.name and (p["fqn"] in fqn or fqn.endswith(table)):
                        entry = client.get_entry(request={"name": de.name, "view": "ALL"})
                        break
            except Exception:
                pass
            if entry is None:
                try:
                    entry = client.lookup_entry(request={
                        "name": scope, "fully_qualified_name": p["fqn"], "view": "ALL"})
                except Exception:
                    pass
            if entry is None:
                print(f"  ✗ {p['product']:24s} entry not found ({p['fqn']})")
                continue
            dp_data = struct_pb2.Struct(); dp_data.update({
                "business_domain": p["domain"], "product_owner": p["owner"],
                "steward": p["steward"], "criticality": p["criticality"],
                "certification_status": p["certification"], "sla": p["sla"],
                "cost_center": p["cost_center"]})
            gov_data = struct_pb2.Struct(); gov_data.update({"pii_classification": p["pii"]})
            entry.aspects[dp_key] = dataplex_v1.Aspect(aspect_type=dp_type, data=dp_data)
            entry.aspects[gov_key] = dataplex_v1.Aspect(aspect_type=gov_type, data=gov_data)
            client.update_entry(request={
                "entry": entry,
                "update_mask": {"paths": ["aspects"]},
                "aspect_keys": [dp_key, gov_key]})
            print(f"  ✓ {p['product']:24s} -> {p['fqn']}")
        except Exception as e:
            print(f"  ✗ {p['product']:24s} {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"== Catalog bootstrap ({ENV}) ==")
    num = project_number()
    print("project number:", num)
    print("-- glossary --"); create_glossary()
    print("-- attach aspects to data products --"); attach_aspects(num)
    print("Done. Search them: gcloud dataplex entries search 'deposit transaction' --project", PROJECT)
