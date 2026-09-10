"""Speak real MCP over stdio to the local server, and call a tool.

The sibling of `verify_mcp_live.py`, which covers the deployed HTTP transport. This one
covers stdio, needs no credentials, and therefore runs in CI.

Why a round-trip rather than more unit tests: the two worst faults this server has had
over stdio were both invisible to unit tests and only reachable through an actual client
session. One was a subprocess that inherited stdin — which over stdio *is* the JSON-RPC
stream — and ate the client's bytes, hanging every call forever. The other was a proxied
Host rejected with a bare 421. Both passed every test in the suite.

`ask_analytics` is the tool exercised because it is the one that composes the most: tool
registration, the staff gate deferring over stdio, the process layer's routing rules, and
a refusal that has to survive as content rather than as an exception.

    python scripts/verify_mcp_stdio.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# (question, expected route). The routing rules live in the process layer; this asserts
# the tool actually consults them, not that the rules themselves are right — that is
# products/process/api/analyst_routing.py's own tests.
CASES = (
    ("what does overdraft mean here", "semantics"),
    ("what is the fee for a returned item", "kb"),
    ("how many accounts went negative last month", "analytics"),
)


async def probe() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["FINCHAT_MCP_PERSONA"] = "analyst"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO / "mcp_server" / "server.py")],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print(f"{len(tools)} tools offered over stdio")
            if "ask_analytics" not in tools:
                print("FAIL: ask_analytics is not registered", file=sys.stderr)
                return 1

            for question, expect in CASES:
                res = await session.call_tool("ask_analytics", {"question": question})
                body = "".join(getattr(b, "text", "") for b in res.content)
                if f'"mode": "{expect}"' not in body:
                    print(f"FAIL: {question!r} did not route to {expect}", file=sys.stderr)
                    print(body[:400], file=sys.stderr)
                    return 1
                print(f"  {expect:<10} ok   {question[:46]}")

                if expect == "analytics":
                    # The refusal is the point. A refusal with no reason and no pointer
                    # is indistinguishable from a bug to whoever reads it.
                    for needed in ("refused", "ADR-0019", "instead"):
                        if needed not in body:
                            print(f"FAIL: refusal is missing {needed!r}", file=sys.stderr)
                            return 1
                    print("             refusal carries its reason and its unblocker")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
