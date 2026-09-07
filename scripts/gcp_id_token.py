"""Mint an OIDC id-token for a private Cloud Run audience, from whatever identity we have.

One implementation, because there are two identities and they behave differently in a way
that is invisible until it is not:

  * **A signed-in human.** `gcloud auth print-identity-token` prints a token, and Cloud
    Run accepts it from anyone holding `run.invoker`. This is what a developer has.
  * **Workload Identity Federation**, which is what CI has. The credentials are an
    external account; there is no id-token to print, and gcloud says exactly that —
    "No identity token can be obtained from the current credentials" — with no hint that
    the fix is to mint one for yourself through a different API.

That difference broke the first scheduled run of `verify-live.yml` in all three
environments, having passed locally every time, which is the same shape as the two
deploy-time faults this repo's guards already exist for: verified in an environment that
did not match the one it runs in.

So: try the human path, fall back to `generateIdToken` on the active service account. The
fallback needs `roles/iam.serviceAccountTokenCreator` on ITSELF — granted in the
foundation module, scoped to that one account rather than the project, because
project-level tokenCreator would let the deploy identity impersonate every service
account in the platform.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def _gcloud(*args: str) -> tuple[int, str]:
    exe = shutil.which("gcloud") or "gcloud"
    out = subprocess.run([exe, *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    return out.returncode, out.stdout.strip()


def _jwt(stdout: str) -> str | None:
    """The JWT in a command's output, ignoring anything else it printed.

    `stdout.strip()` is the obvious spelling and is wrong: the gcloud launcher can emit a
    line of its own first, and the whole blob then goes into an Authorization header,
    which is rejected with a message about return characters that never mentions gcloud.
    """
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.count(".") == 2 and " " not in line and len(line) > 100:
            return line
    return None


def active_account() -> str:
    code, out = _gcloud("auth", "list", "--filter=status:ACTIVE",
                        "--format=value(account)")
    return out.splitlines()[0].strip() if code == 0 and out else ""


def id_token(audience: str) -> str | None:
    """An id-token for `audience`, or None. Never raises; prints why on failure."""
    code, out = _gcloud("auth", "print-identity-token")
    token = _jwt(out) if code == 0 else None
    if token:
        return token

    account = active_account()
    if not account or not account.endswith(".gserviceaccount.com"):
        print(f"id-token: no service account to impersonate (active: {account or 'none'})",
              file=sys.stderr)
        return None

    code, access = _gcloud("auth", "print-access-token")
    if code != 0 or not access.strip():
        print("id-token: could not obtain an access token", file=sys.stderr)
        return None

    url = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
           f"{account}:generateIdToken")
    body = json.dumps({"audience": audience, "includeEmail": True}).encode()
    request = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {access.strip()}",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode()).get("token")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        print(f"id-token: generateIdToken for {account} failed ({exc.code}). "
              f"Does it hold roles/iam.serviceAccountTokenCreator on itself? {detail}",
              file=sys.stderr)
    except Exception as exc:
        print(f"id-token: {type(exc).__name__}: {exc}", file=sys.stderr)
    return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: gcp_id_token.py <audience>", file=sys.stderr)
        raise SystemExit(2)
    minted = id_token(sys.argv[1])
    if not minted:
        raise SystemExit(1)
    print(minted)
