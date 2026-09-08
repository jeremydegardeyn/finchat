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
import urllib.error
import urllib.request
from pathlib import Path

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


class GcloudFailed(RuntimeError):
    """gcloud could not answer. Distinct from "the thing does not exist".

    Returning "" for both made an expired local session report
    `finchat-prod-mcp is not deployed`, which is a different and much more alarming
    claim than the truth, which was "I could not ask".
    """


def _gcloud(*args: str) -> str:
    exe = shutil.which("gcloud") or "gcloud"
    out = subprocess.run([exe, *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        message = (out.stderr or out.stdout).strip()
        first = message.splitlines()[0] if message else "gcloud failed"
        if "Cannot find service" in message or "NOT_FOUND" in message:
            # gcloud answered, and the answer is "it does not exist". That is a fact
            # about the world, not a failure to reach it.
            return ""
        raise GcloudFailed(first)
    return out.stdout.strip()


def _anonymous(url: str) -> tuple[int, str]:
    """Status and WWW-Authenticate for a request carrying no credential at all.

    On a public, OAuth-enforcing endpoint this is what EVERY caller is until they
    authenticate, and Cloud Run is no longer refusing them on our behalf — so it is the
    contract most worth watching.
    """
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            return r.status, r.headers.get("www-authenticate", "")
    except urllib.error.HTTPError as e:
        return e.code, (e.headers or {}).get("www-authenticate", "")
    except Exception:
        return 0, ""


def _token(audience: str) -> str | None:
    """Delegated to `gcp_id_token`, which knows that a human and a federated CI identity
    mint id-tokens by different routes — a difference that broke the first scheduled run
    of this check in every environment after passing locally every time."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gcp_id_token import id_token

    return id_token(audience)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--project", default="strongsville-city-schools")
    args = ap.parse_args()

    name = f"finchat-{args.env}-mcp"
    try:
        url = _gcloud("run", "services", "describe", name, "--region", args.region,
                      "--project", args.project, "--format=value(status.url)")
    except GcloudFailed as exc:
        # Exit 2, not 1: "I could not ask" is an operator problem with this runner, and
        # reporting it as "the service is missing" sends someone to look at production
        # for a fault that is in their own shell.
        print(f"could not query {name}: {exc}", file=sys.stderr)
        return 2
    if not url:
        print(f"{name} is not deployed in {args.project}", file=sys.stderr)
        return 1

    token = _token(url)
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

    # When the endpoint is public and enforcing OAuth (ADR-0020), the interesting
    # assertions are what an ANONYMOUS caller gets — because that is now everyone until
    # they authenticate, and Cloud Run is no longer refusing them on our behalf.
    public_checks = []
    anon_code, challenge_header = _anonymous(f"{url}/mcp")
    if anon_code == 401:
        if "resource_metadata=" not in (challenge_header or ""):
            public_checks.append("401 carries no resource_metadata — a hosted client "
                                 "cannot discover where to authenticate")
        # The RFC 9728 form specifically: a client that builds this itself, rather than
        # following our header, is the spec-compliant one and must not get a 401.
        meta_code, _ = _anonymous(f"{url}/.well-known/oauth-protected-resource/mcp")
        if meta_code != 200:
            public_checks.append(
                f"RFC 9728 discovery returned {meta_code}; a compliant client that "
                "constructs the URL itself cannot find the authorization server")
    elif anon_code not in (403, 0):
        public_checks.append(f"anonymous request returned {anon_code} — expected 401 "
                             "(public + enforcing) or 403 (private)")

    # One retry, on transport only. A scale-to-zero service occasionally refuses a cold
    # connect, and last night's scheduled run failed on exactly that while the run ten
    # hours earlier passed on the same commit. A monitor that cries wolf gets ignored,
    # which costs more than the flake did.
    #
    # The retry is REPORTED rather than silent: a service that needs a second attempt
    # every night is a real signal, and swallowing it would trade a noisy check for a
    # blind one.
    got = None
    retry_note = ""
    failure: Exception | None = None
    try:
        got = anyio.run(probe)
    except Exception as first:
        failure = first
        # A 401 or 403 is a decision, and it will be the same decision five seconds
        # later. Only a transport failure can succeed on a second attempt — and one is
        # worth making, because a scale-to-zero service occasionally refuses a cold
        # connect: last night's scheduled run failed on exactly that while the run ten
        # hours earlier passed on the same commit.
        detail = " ".join(str(e) for e in
                          (getattr(first, "exceptions", None) or [first]))
        if not any(code in detail for code in ("401", "403")):
            import time as _time

            _time.sleep(5)
            try:
                got = anyio.run(probe)
                failure = None
                # Reported, not silent. A service needing a second attempt every night
                # is a real signal, and hiding it trades a noisy check for a blind one.
                retry_note = (f" (first attempt failed: {type(first).__name__}; "
                              "retry succeeded)")
            except Exception as second:
                failure = second

    try:
        if failure is not None:
            raise failure
    except Exception as exc:
        # "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)" is what the
        # MCP client raises for every transport failure, and it names none of them. The
        # sub-exception carries the status; a 401 here means this caller is not in
        # FINCHAT_MCP_SERVICE_CALLERS *on the running service*, which is a different
        # thing from the variable being set — env vars are baked at deploy time.
        detail = "; ".join(f"{type(e).__name__}: {e}"
                           for e in getattr(exc, "exceptions", []) or [exc])
        print(f"{name}: {type(exc).__name__}: {detail}", file=sys.stderr)
        if anon_code == 401 and not public_checks:
            # A HUMAN running this against a public, OAuth-enforcing endpoint is refused
            # by design: people authenticate through the proxy (ADR-0020), and only named
            # services use a Google token. That is the endpoint working, not failing — so
            # report what was proved, say what was skipped, and do not cry wolf twice a
            # day at whoever runs this from a laptop.
            print(f"{name}: anonymous contract OK (401 + discovery). Tool listing skipped "
                  "— this identity is not a permitted service caller, which is correct "
                  "for a human; use the OAuth flow, or run this as a listed service.")
            return 0
        print(f"{name}: could not list tools. If this caller SHOULD be allowed as a "
              "service it must be in FINCHAT_MCP_SERVICE_CALLERS *and* the service "
              "redeployed since that variable changed — env vars are baked at deploy "
              "time, so setting the variable alone changes nothing.", file=sys.stderr)
        return 1

    print(f"{name}: {got['server']} — {len(got['tools'])} tools, "
          f"{got['resources']} resources, {got['instructions']} chars of instructions"
          f"{retry_note}")

    problems = list(public_checks)
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
