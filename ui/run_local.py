"""Run the BFF on a laptop against DEPLOYED backends.

The three ways to run this UI are all-local (demo repositories, no GCP), all-deployed,
and this one — the local page against the real txn-api, loan-api and agent. It is the
configuration where integration bugs actually live, and until now it was not possible:
`_id_token` only knew the metadata identity endpoint, which a laptop does not have, so
every call went out unauthenticated and came back 403.

It works because `_id_token` now falls back to the token gcloud already holds. Cloud Run
accepts that from anyone with `run.invoker`, and it has a property the deployed path
cannot have: the call is attributable to a *person*, so ADR-0019 column-level security
is evaluated against them rather than a shared service identity. What you see here is
what your own entitlements allow, which is the point of that ADR and hard to demonstrate
any other way.

    python ui/run_local.py            # dev
    python ui/run_local.py --env test

No `GOOGLE_OAUTH_CLIENT_ID` is set, so the page runs the anonymous/guest tier rather than
prompting for sign-in. Pick a persona with `?persona=customer` (or employee, analyst,
admin) — the view does not render until one is chosen.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGION = os.getenv("REGION", "us-central1")
PROJECT = os.getenv("GCP_PROJECT", "strongsville-city-schools")


def service_url(name: str) -> str:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        return ""
    out = subprocess.run(
        [gcloud, "run", "services", "describe", name, "--region", REGION,
         "--project", PROJECT, "--format=value(status.url)"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return out.stdout.strip() if out.returncode == 0 else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--port", type=int, default=8091)
    args = ap.parse_args()

    urls = {key: service_url(f"finchat-{args.env}-{svc}") for key, svc in (
        ("TXN_API_URL", "txn-api"), ("LOAN_API_URL", "loan-api"), ("AGENT_URL", "agent"))}
    missing = [k for k, v in urls.items() if not v]
    if missing:
        print(f"Could not resolve {missing} for env {args.env!r}. Is gcloud authenticated "
              f"and is the environment deployed?", file=sys.stderr)
        return 2

    os.environ.update(urls)
    os.environ.update({
        "GCP_PROJECT": PROJECT,
        "GOLD_DATASET": f"finchat_gold_{args.env}",
        "SILVER_DATASET": f"finchat_silver_{args.env}",
        "LOANS_DATASET": f"finchat_loans_{args.env}",
    })
    # Cleared rather than merely unset: an inherited client id from another shell puts a
    # sign-in button on a page whose origin that OAuth client has never heard of.
    os.environ.pop("GOOGLE_OAUTH_CLIENT_ID", None)
    sys.path.insert(0, str(HERE))

    for key, value in urls.items():
        print(f"  {key:<14} {value}")
    print(f"\nhttp://localhost:{args.port}/?persona=customer\n")

    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
