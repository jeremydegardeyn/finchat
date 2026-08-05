#!/usr/bin/env python3
"""
Publish the agent registry to BigQuery (ADR-0023).

`agents_catalog.py` is the source of truth; this projects it into
`finchat_platform_<env>.agent_registry` so the Admin UI, an auditor, or an examiner can
query "what agents are running, who owns them, what may they touch, when were they last
recertified" without reading Python.

The table is a **current-state snapshot**, replaced on every run — history lives in git
for the catalogue and in `agent_action_log` for behaviour. Run it after every deploy:

    python scripts/agent_registry_bootstrap.py dev
    python scripts/agent_registry_bootstrap.py dev --dry-run

Refuses to publish a registry that fails verification, so a drifted registry can never
become the thing an examiner is shown.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents_catalog import agents, recert_due, service_account_id  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252.
except Exception:
    pass

PROJECT = "strongsville-city-schools"
HERE = Path(__file__).resolve().parent


def _sa_email(agent: dict, env: str) -> str:
    return f"{service_account_id(agent, env)}@{PROJECT}.iam.gserviceaccount.com"


def build_rows(env: str) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for a in agents(env):
        if a["status"] != "active":
            continue
        rows.append({
            "agent_id": a["id"],
            "display": a["display"],
            "product": a["product"],
            "kind": a["kind"],
            "runtime": a["runtime"],
            "service_account": _sa_email(a, env),
            "owner": a["owner"],
            "business_area": a["business_area"],
            "risk_tier": a["risk_tier"],
            "tools": a["tools"],
            "data_scope": a["data_scope"],
            "model_alias": a["model_alias"],
            "model_ref": a["model_ref"],
            "consequential": a["consequential"],
            "hitl": a["hitl"],
            "registered": a["registered"],
            "last_recertified": a["last_recertified"],
            "recert_due": recert_due(a).isoformat(),
            "status": a["status"],
            "published_at": now,
        })
    return rows


def verify(env: str) -> bool:
    """Never publish a registry that fails its own gate."""
    proc = subprocess.run(
        [sys.executable, str(HERE / "verify_agent_registry.py"), "--env", env],
        capture_output=True, text=True)
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, end="")
    return proc.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish the FinChat agent registry to BigQuery.")
    ap.add_argument("env", nargs="?", default="dev")
    ap.add_argument("--dry-run", action="store_true", help="print rows, write nothing")
    ap.add_argument("--skip-verify", action="store_true",
                    help="publish without the registry gate (use only to debug)")
    args = ap.parse_args()

    if not args.skip_verify and not verify(args.env):
        print("registry verification FAILED — refusing to publish.")
        return 1

    rows = build_rows(args.env)
    table = f"{PROJECT}.finchat_platform_{args.env}.agent_registry"

    print(f"\n== agent registry ({args.env}) — {len(rows)} agents ==")
    for r in rows:
        due = date.fromisoformat(r["recert_due"])
        print(f"  {r['agent_id']:<26} {r['risk_tier']:<7} {r['service_account']}")
        print(f"  {'':<26} owner {r['owner']}  ·  recert {due.isoformat()}  ·  "
              f"tools {', '.join(r['tools']) or '—'}")

    if args.dry_run:
        print(f"\n[dry-run] would replace {table}")
        print(json.dumps(rows[:1], indent=2))
        return 0

    from google.cloud import bigquery
    bq = bigquery.Client(project=PROJECT)

    # Snapshot semantics: truncate then load, in one job, so a reader never sees a
    # half-published registry.
    job = bq.load_table_from_json(
        rows, table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema_update_options=None,
        ),
    )
    job.result()
    print(f"\npublished {len(rows)} agents -> {table}")
    print(f"query: SELECT agent_id, owner, risk_tier, recert_due FROM `{table}` "
          f"ORDER BY recert_due")
    return 0


if __name__ == "__main__":
    sys.exit(main())
