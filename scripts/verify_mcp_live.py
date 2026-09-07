"""Connect to a deployed MCP server over HTTP and assert it still offers its tools.

The narrow thing this catches that nothing else does: the agent channel's transport.
`tools/list` exercises streamable HTTP, the OIDC audience, and the SDK's DNS-rebinding
protection — the last of which rejects a proxied Host with a bare `421` that mentions
neither hosts nor rebinding, and which no unit test can reach because there is no proxy
in front of a local server.

It calls no tool and reads no account. A monitoring job that fetches a balance puts that
balance in a CI log that outlives the run, and this one runs on a schedule against prod.

    python scripts/verify_mcp_live.py --env prod
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

# A deploy that serves an empty or truncated catalogue is a working HTTP endpoint and a
# broken channel. These are the tools every persona sees; the approver-only ones are
# deliberately absent, because `FINCHAT_MCP_PERSONA=customer` hides them and asserting on
# them would make the check fail for a correct configuration.
EXPECTED = {
    "finchat_status",
    "get_account_balance",
    "get_account_transactions",
    "get_customer_overview",
    "get_loan_status",
    "search_knowledge_base",
    "describe_data_model",
}


def _gcloud(*args: str) -> str:
    exe = shutil.which("gcloud") or "gcloud"
    out = subprocess.run([exe, *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    return out.stdout.strip() if out.returncode == 0 else ""


def _token() -> str | None:
    """The JWT gcloud holds — matched by shape, not taken as the whole of stdout.

    The launcher can print a line of its own first, and the resulting Authorization
    header is rejected with a message about return characters that says nothing about
    gcloud.
    """
    for line in reversed(_gcloud("auth", "print-identity-token").splitlines()):
        line = line.strip()
        if line.count(".") == 2 and " " not in line and len(line) > 100:
            return line
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--project", default="strongsville-city-schools")
    args = ap.parse_args()

    name = f"finchat-{args.env}-mcp"
    url = _gcloud("run", "services", "describe", name, "--region", args.region,
                  "--project", args.project, "--format=value(status.url)")
    if not url:
        print(f"{name} is not deployed in {args.project}", file=sys.stderr)
        return 1

    token = _token()
    if not token:
        print("no identity token — is gcloud authenticated?", file=sys.stderr)
        return 2

    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def probe() -> dict:
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(f"{url}/mcp", headers=headers,
                                         timeout=120) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                resources = await session.list_resources()
                return {
                    "server": init.serverInfo.name,
                    "instructions": len(init.instructions or ""),
                    "tools": {t.name for t in tools.tools},
                    "resources": len(resources.resources),
                }

    try:
        got = anyio.run(probe)
    except Exception as exc:
        print(f"{name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"{name}: {got['server']} — {len(got['tools'])} tools, "
          f"{got['resources']} resources, {got['instructions']} chars of instructions")

    problems = []
    missing = sorted(EXPECTED - got["tools"])
    if missing:
        problems.append(f"missing tools: {missing}")
    if not got["resources"]:
        problems.append("no resources offered — the knowledge plane is not being served")
    # The refusal policy travels in `instructions`, and it is the only place the protocol
    # lets this server constrain a client's model. An empty one is a working endpoint
    # serving answers the platform cannot stand behind.
    if got["instructions"] < 500:
        problems.append(f"instructions are {got['instructions']} chars — refusal policy "
                        "missing or truncated")

    for problem in problems:
        print(f"::error::{name}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
