"""A Lambda that calls FinChat's MCP endpoint from AWS, holding no credential.

Why Lambda and not App Runner, which is the obvious Cloud Run analogue: App Runner has
no scale-to-zero and bills provisioned memory while idle (~$5/month for a thing that is
usually asleep). Lambda's free tier — 1M requests and 400k GB-seconds a month — is
perpetual rather than a trial, so a demo harness costs nothing at all.

The credential story is the interesting part. Lambda has **no EC2 metadata service**, and
Workload Identity Federation is usually described in terms of one. It works anyway:
google-auth checks `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`
before it tries IMDS, and reads `AWS_REGION` the same way — its own source says "The AWS
metadata server is not available in some AWS environments such as AWS lambda." Lambda
sets all four for the execution role on every invocation. So the identity this runs as is
the **execution role**, and nothing but a role ARN ever has to be trusted.

Invoke it with a tool name and arguments:

    aws lambda invoke --function-name finchat-mcp-harness \\
      --payload '{"tool": "get_account_balance", "arguments": {"account_id": "ACC001"}}' \\
      /dev/stdout

Defaults to `finchat_status`, which reads no customer data and is safe in a CloudWatch
log — logs outlive the invocation and are a place account data should not accumulate.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GENERATE = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            "{sa}:generateIdToken")
DEFAULT_TOOL = "finchat_status"


class HarnessError(RuntimeError):
    """Something the caller can act on. The message says which."""


def audience(url: str) -> str:
    """The service origin, with no path.

    `FINCHAT_MCP_URL` ends in `/mcp` because that is the endpoint, but Cloud Run and
    `mcp_server/auth.py` both check the audience against the service URL. A token
    audienced to the full path is refused with a plain 401 that never mentions the path.
    """
    parts = url.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else url


def id_token(target_audience: str, service_account: str) -> str:
    """Exchange this Lambda's execution role for a Google id-token.

    Deliberately not `google.oauth2.id_token.fetch_id_token`: given a WIF credential
    configuration that raises "Neither metadata server or valid service account
    credentials are found", a message about missing credentials for a file that is
    present and correct. It handles `service_account` and `impersonated_service_account`
    files and the metadata server, and `external_account` is none of those.

    `generateIdToken` is callable here because `roles/iam.workloadIdentityUser` — which
    the pool binding grants to this role's principalSet — includes
    `iam.serviceAccounts.getOpenIdToken`.
    """
    import google.auth
    from google.auth.transport.requests import Request

    try:
        source, _ = google.auth.default(scopes=[SCOPE])
        source.refresh(Request())
    except Exception as exc:
        raise HarnessError(
            f"no federated credentials ({type(exc).__name__}: {exc}). Is "
            "GOOGLE_APPLICATION_CREDENTIALS pointing at the credential configuration, "
            "and does the pool's attribute condition match this execution role?"
        ) from None

    body = json.dumps({"audience": target_audience, "includeEmail": True}).encode()
    request = urllib.request.Request(
        GENERATE.format(sa=service_account), data=body,
        headers={"Authorization": f"Bearer {source.token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            minted = json.loads(response.read().decode()).get("token")
    except urllib.error.HTTPError as exc:
        raise HarnessError(
            f"generateIdToken for {service_account} failed ({exc.code}): "
            f"{exc.read().decode()[:300]}") from None
    if not minted:
        raise HarnessError("generateIdToken returned no token")
    return minted


async def _call(url: str, token: str, tool: str, arguments: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers, timeout=45) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted(t.name for t in listed.tools)
            if tool not in names:
                # Not an error in the transport — the identity was simply not offered
                # this tool. Saying so beats a generic failure, because for a SERVICE
                # caller it is often the correct answer (see docs/29).
                return {"ok": False, "tools": names,
                        "error": f"{tool} is not offered to this identity"}
            result = await session.call_tool(tool, arguments)
            content = [getattr(b, "text", str(b)) for b in result.content]
            return {"ok": not result.isError, "tools": names, "result": content}


def _causes(exc: BaseException, depth: int = 0) -> list[str]:
    """Flatten an ExceptionGroup or cause chain.

    The MCP client runs inside a TaskGroup, so an unwrapped failure reads "unhandled
    errors in a TaskGroup (1 sub-exception)" and hides the HTTP status, which is the only
    fact worth having.
    """
    out = [f"{'  ' * depth}{type(exc).__name__}: {exc}"]
    for inner in getattr(exc, "exceptions", []) or []:
        out += _causes(inner, depth + 1)
    if exc.__cause__ is not None:
        out += _causes(exc.__cause__, depth + 1)
    return out


def lambda_handler(event, context):  # noqa: ARG001 — Lambda's signature
    event = event or {}
    url = os.environ.get("FINCHAT_MCP_URL", "")
    service_account = os.environ.get("FINCHAT_MCP_SERVICE_ACCOUNT", "")
    if not url or not service_account:
        return {"ok": False,
                "error": "set FINCHAT_MCP_URL and FINCHAT_MCP_SERVICE_ACCOUNT"}

    tool = event.get("tool") or DEFAULT_TOOL
    arguments = event.get("arguments") or {}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "`arguments` must be an object"}

    try:
        token = id_token(audience(url), service_account)
    except HarnessError as exc:
        return {"ok": False, "error": str(exc)}

    try:
        return asyncio.run(_call(url, token, tool, arguments))
    except BaseException as exc:
        return {"ok": False, "error": _causes(exc)}
