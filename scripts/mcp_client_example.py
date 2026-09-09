"""Call the deployed MCP endpoint the way an AWS workload does (docs/29-mcp-from-aws.md).

Runs unchanged in three places, which is the point — a failure in AWS can be compared
against a known-good run on a laptop:

  * **In AWS**, with `GOOGLE_APPLICATION_CREDENTIALS` pointing at a Workload Identity
    Federation credential configuration. Pass `--service-account`.
  * **Anywhere in GCP** with a metadata server (Cloud Run, GCE), with no arguments beyond
    the URL.
  * **On a developer laptop**, falling back to gcloud.

The id-token part is the part worth reading. The obvious call is
`google.oauth2.id_token.fetch_id_token`, and for a WIF credential configuration it raises
"Neither metadata server or valid service account credentials are found" — a message about
missing credentials for a file that is present and valid. Its source explains why: it
handles `service_account` and `impersonated_service_account` files and the metadata
server, and an `external_account` file is none of those. So this mints the token
explicitly through `generateIdToken`, which the federated principal may call because
`roles/iam.workloadIdentityUser` includes `iam.serviceAccounts.getOpenIdToken`.

    python scripts/mcp_client_example.py --url https://finchat-dev-mcp-xxxx.run.app/mcp

Exit codes: 0 the endpoint answered and the tool ran; 1 it refused or failed;
2 no credential could be obtained, which is a different problem from a refusal.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GENERATE = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            "{sa}:generateIdToken")


def _audience(url: str) -> str:
    """What Google mints a service token for: the service origin, with no path.

    `--url` carries `/mcp` because that is the MCP endpoint, but Cloud Run and
    `mcp_server/auth.py` both check the audience against the service URL. Sending a token
    audienced to the full path fails with a plain 401 and no hint that the path is why.
    """
    parts = url.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else url


def _via_impersonation(audience: str, service_account: str) -> str | None:
    """ADC's access token, spent on `generateIdToken` for the named service account."""
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError:
        print("id-token: google-auth is not installed", file=sys.stderr)
        return None

    try:
        source, _ = google.auth.default(scopes=[SCOPE])
        source.refresh(Request())
    except Exception as exc:
        print(f"id-token: no application default credentials ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return None

    body = json.dumps({"audience": audience, "includeEmail": True}).encode()
    request = urllib.request.Request(
        GENERATE.format(sa=service_account), data=body,
        headers={"Authorization": f"Bearer {source.token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode()).get("token")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        print(f"id-token: generateIdToken for {service_account} failed ({exc.code}). "
              "Is the federated principal bound to it with roles/iam.workloadIdentityUser, "
              f"and does the attribute condition match the assumed role? {detail}",
              file=sys.stderr)
    except Exception as exc:
        print(f"id-token: {type(exc).__name__}: {exc}", file=sys.stderr)
    return None


def _via_metadata(audience: str) -> str | None:
    """The metadata server, for a caller already running as a service account in GCP."""
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token as gid

        return gid.fetch_id_token(google.auth.transport.requests.Request(), audience)
    except Exception:
        return None


def token_for(audience: str, service_account: str | None) -> str | None:
    if service_account:
        return _via_impersonation(audience, service_account)
    minted = _via_metadata(audience)
    if minted:
        return minted
    try:
        import gcp_id_token
    except ImportError:
        return None
    return gcp_id_token.id_token(audience)


async def _call(url: str, token: str, tool: str, arguments: dict) -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers, timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            print(f"{len(names)} tools: {', '.join(names)}")
            if tool not in names:
                print(f"{tool} is not offered to this identity", file=sys.stderr)
                return 1
            result = await session.call_tool(tool, arguments)
            for block in result.content:
                print(getattr(block, "text", block))
            return 1 if result.isError else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the MCP endpoint, ending in /mcp")
    parser.add_argument("--service-account",
                        help="mint the id-token for this service account via "
                             "generateIdToken; required under WIF from AWS")
    parser.add_argument("--tool", default="finchat_status",
                        help="tool to call (default: finchat_status, which reads no "
                             "customer data and is safe in a log)")
    parser.add_argument("--args", default="{}", help="tool arguments as JSON")
    options = parser.parse_args()

    try:
        arguments = json.loads(options.args)
    except json.JSONDecodeError as exc:
        print(f"--args is not JSON: {exc}", file=sys.stderr)
        return 2

    audience = _audience(options.url)
    token = token_for(audience, options.service_account)
    if not token:
        print(f"could not mint an id-token for {audience}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(_call(options.url, token, options.tool, arguments))
    except BaseException as exc:
        # The MCP client runs inside a TaskGroup, so a plain `except Exception` prints
        # "unhandled errors in a TaskGroup (1 sub-exception)" and hides the only useful
        # line — which is usually an HTTP status. Unwrap it.
        for line in _causes(exc):
            print(line, file=sys.stderr)
        print(f"probe: {' '.join(str(p) for p in _probe(options.url, token))}",
              file=sys.stderr)
        return 1


def _causes(exc: BaseException, depth: int = 0) -> list[str]:
    """Every exception in a group or cause chain, flattened."""
    pad = "  " * depth
    out = [f"{pad}{type(exc).__name__}: {exc}"]
    for inner in getattr(exc, "exceptions", []) or []:
        out += _causes(inner, depth + 1)
    if exc.__cause__ is not None:
        out += _causes(exc.__cause__, depth + 1)
    return out


def _probe(url: str, token: str) -> tuple[int, str]:
    """What the endpoint says to a plain HTTP request, which names the real problem.

    A 401 here with a `WWW-Authenticate` header is the resource server refusing the
    identity — a different thing from Cloud Run's 403, and from a transport fault.
    """
    request = urllib.request.Request(
        url, data=b"{}", headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json",
                                  "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.headers.get("WWW-Authenticate")
                          or exc.read().decode()[:200] or "")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    raise SystemExit(main())
