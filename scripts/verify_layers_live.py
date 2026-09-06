"""Walk every API layer of a deployed environment and report what actually answered.

Not a test — a demonstration that runs against real infrastructure and prints evidence.
The unit tests prove the code is right in isolation; this proves the *wiring* is right,
which is where every failure of the last two increments actually lived: a missing
dependency, an unset URL, an IAM grant nobody made.

    python scripts/verify_layers_live.py --env dev
    python scripts/verify_layers_live.py --env dev --account acct-001 --json

It authenticates as you, via `gcloud auth print-identity-token`, so it also answers the
question "can a human with run.invoker reach this" — which is a different question from
"can the service reach it", and the two have failed independently.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REGION = "us-central1"
PROJECT = "strongsville-city-schools"

# One row per hop, ordered so a failure lands on the lowest broken layer rather than on
# whatever the topmost caller reported. That ordering is the whole value of the script:
# "mobile says the process API is unavailable" and "the process API cannot reach txn-api"
# look identical from the top and are entirely different problems.
LAYERS = [
    ("system", "txn-api", "/v1/accounts/{account}/balance"),
    ("system", "loan-api", "/v1/loans?account_id={account}"),
    ("process", "process", "/v1/customers/by-account/{account}/overview"),
    ("experience", "mobile", "/v1/home?account_id={account}"),
]


def sh(*args: str) -> str:
    out = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return out.stdout.strip() if out.returncode == 0 else ""


def service_url(name: str) -> str:
    return sh(shutil.which("gcloud") or "gcloud", "run", "services", "describe", name,
              "--region", REGION, "--project", PROJECT, "--format=value(status.url)")


def token() -> str:
    return sh(shutil.which("gcloud") or "gcloud", "auth", "print-identity-token")


def get(url: str, tok: str, timeout: float = 90) -> tuple[int, object, float]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "Authorization": f"Bearer {tok}"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null"), time.time() - started
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200], time.time() - started
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", time.time() - started


def mcp_over_http(url: str, tok: str, account: str) -> dict:
    """Connect to the deployed MCP server as a real client and call one tool.

    This is the leg that cannot be faked locally. stdio and HTTP share the tool code and
    nothing else: a different transport, a different identity, a different failure mode
    (the SDK's DNS-rebinding protection answers a proxied Host with a bare 421 that
    mentions neither hosts nor rebinding).
    """
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        headers = {"Authorization": f"Bearer {tok}"}
        async with streamablehttp_client(f"{url}/mcp", headers=headers, timeout=120) as (r, w, _):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = await s.list_tools()
                resources = await s.list_resources()
                started = time.time()
                res = await s.call_tool("get_customer_overview", {"account_id": account})
                elapsed = time.time() - started
                text = "\n".join(c.text for c in res.content
                                 if getattr(c, "type", "") == "text")
                return {
                    "server": init.serverInfo.name,
                    "instructions_chars": len(init.instructions or ""),
                    "tools": [t.name for t in tools.tools],
                    "resources": [str(r.uri) for r in resources.resources],
                    "called": "get_customer_overview",
                    "seconds": round(elapsed, 2),
                    "result": text[:900],
                }

    return anyio.run(run)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--account", default="acct-001")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    tok = token()
    if not tok:
        print("No identity token. Run `gcloud auth login` first.", file=sys.stderr)
        return 2

    report: dict = {"env": args.env, "account": args.account, "hops": []}
    for layer, service, path in LAYERS:
        name = f"finchat-{args.env}-{service}"
        url = service_url(name)
        if not url:
            report["hops"].append({"layer": layer, "service": service,
                                   "status": "not deployed"})
            continue
        status, body, secs = get(url + path.format(account=args.account), tok)
        report["hops"].append({
            "layer": layer, "service": service, "url": url, "http": status,
            "seconds": round(secs, 2),
            "body": body if isinstance(body, str) else _summarise(body),
        })

    mcp_url = service_url(f"finchat-{args.env}-mcp")
    if mcp_url:
        try:
            report["mcp"] = {"url": mcp_url, **mcp_over_http(mcp_url, tok, args.account)}
        except Exception as e:
            report["mcp"] = {"url": mcp_url, "error": f"{type(e).__name__}: {e}"}
    else:
        report["mcp"] = {"error": "finchat-%s-mcp is not deployed" % args.env}

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"\nFinChat layers — {args.env} — account {args.account}\n")
    for hop in report["hops"]:
        mark = "ok " if hop.get("http") == 200 else "FAIL"
        print(f"  [{mark}] {hop['layer']:<10} {hop['service']:<10} "
              f"{hop.get('http', '-'):>4}  {hop.get('seconds', '-'):>6}s  "
              f"{hop.get('body', hop.get('status'))}")

    mcp = report["mcp"]
    print("\n  MCP over HTTP")
    if "error" in mcp:
        print(f"  [FAIL] {mcp['error']}")
        return 1
    print(f"  [ok ] {mcp['server']} — {len(mcp['tools'])} tools, "
          f"{len(mcp['resources'])} resources, "
          f"{mcp['instructions_chars']} chars of refusal policy shipped as instructions")
    print(f"         {mcp['called']} in {mcp['seconds']}s:")
    for line in mcp["result"].splitlines()[:14]:
        print("           " + line)
    print()
    return 0 if all(h.get("http") == 200 for h in report["hops"]) else 1


def _summarise(body) -> str:
    """One line per hop. A full overview payload is 40 lines and buries the answer."""
    if isinstance(body, list):
        return f"{len(body)} rows"
    if not isinstance(body, dict):
        return str(body)[:120]
    for keys in (("balance", "currency"), ("headline", "balance"), ("loan_id", "status")):
        if all(k in body for k in keys):
            return ", ".join(f"{k}={body[k]}" for k in keys)
    return ", ".join(list(body)[:6])


if __name__ == "__main__":
    raise SystemExit(main())
