"""Drive the MCP OAuth flow against a deployed proxy, end to end (ADR-0020).

Two modes, because only one of them can run unattended.

`--checks` (the default when no browser is wanted) exercises everything that does not
need a human: discovery, JWKS, dynamic client registration, and the refusals — a bad
redirect_uri, PKCE downgrade, an unlisted resource, a bogus authorization code. This is
what CI or an unattended session can prove.

`--login` runs the real thing. It registers itself as a native client on a loopback
redirect (RFC 8252), opens a browser, catches the code, exchanges it with the PKCE
verifier and prints the resulting token's claims. It needs a person signed in to Google,
which is exactly the leg no script can fake — and the leg that proves the access policy
is real, because an identity outside the allowed domain gets refused here rather than
anywhere a test could reach.

    python scripts/verify_oauth_live.py --env dev
    python scripts/verify_oauth_live.py --env dev --login
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

OK, BAD = "  [ok ]", "  [FAIL]"


def gcloud(*args: str) -> str:
    exe = shutil.which("gcloud") or "gcloud"
    out = subprocess.run([exe, *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    return out.stdout.strip() if out.returncode == 0 else ""


def get(url: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def post(url: str, body: dict, form: bool = False) -> tuple[int, object]:
    if form:
        data, ctype = urllib.parse.urlencode(body).encode(), "application/x-www-form-urlencoded"
    else:
        data, ctype = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def redirect_of(url: str) -> str:
    """Follow nothing: the Location header IS the assertion."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=30) as r:
            return r.headers.get("location", "")
    except urllib.error.HTTPError as e:
        return e.headers.get("location", "") if e.headers else ""
    except Exception:
        return ""


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def checks(issuer: str, resource: str) -> int:
    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        print(f"{OK if ok else BAD} {label}{(' — ' + detail) if detail and not ok else ''}")
        failures += 0 if ok else 1

    status, meta = get(f"{issuer}/.well-known/oauth-authorization-server")
    check("RFC 8414 discovery", status == 200 and isinstance(meta, dict), str(meta)[:120])
    if not isinstance(meta, dict):
        return 1

    check("PKCE S256 only, never plain",
          meta.get("code_challenge_methods_supported") == ["S256"])
    check("resource indicators advertised", meta.get("resource_indicators_supported") is True)
    check("signing key is durable, not per-instance",
          meta.get("x_finchat_signing_key_ephemeral") is False,
          "an ephemeral key rejects tokens it just issued after a cold start")
    check("client store is durable",
          meta.get("x_finchat_store_durable") is True,
          "an in-memory store cannot redeem a code on a second instance")
    check("deployment is configured", meta.get("x_finchat_configured") is True,
          "identity provider or allow-lists are unset — it will refuse everyone")

    status, jwks = get(meta.get("jwks_uri", ""))
    check("JWKS published", status == 200 and bool((jwks or {}).get("keys")))

    status, client = post(f"{issuer}/register", {
        "client_name": "finchat-verify",  # the exact name the proxy prunes
        "redirect_uris": ["http://127.0.0.1:47821/cb"]})
    check("dynamic client registration (RFC 7591)", status == 201, str(client)[:140])
    if status != 201:
        return failures
    client_id = client["client_id"]

    verifier, challenge = pkce()

    def authorize(**over) -> str:
        params = {"client_id": client_id, "redirect_uri": "http://127.0.0.1:47821/cb",
                  "response_type": "code", "code_challenge": challenge,
                  "code_challenge_method": "S256", "state": "verify",
                  "resource": resource, "scope": "mcp:tools"}
        params.update(over)
        return redirect_of(f"{issuer}/authorize?{urllib.parse.urlencode(params)}")

    handoff = authorize()
    check("a valid request is handed to Google", "accounts.google.com" in handoff)

    # Follow it to Google and read the answer WITHOUT signing in. Google matches
    # redirect_uri by exact string, and the registration lives in the Cloud Console,
    # which no Terraform or gcloud reconciles — so it can be edited away and nothing
    # here would notice until a person failed to log in. The failure page names it.
    if "accounts.google.com" in handoff:
        try:
            with urllib.request.urlopen(handoff, timeout=30) as r:
                page = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            page = e.read().decode("utf-8", "replace")
        except Exception as e:
            page = f"{type(e).__name__}: {e}"
        check("Google accepts the proxy's registered redirect_uri",
              "redirect_uri_mismatch" not in page,
              "add {issuer}/callback to the OAuth client's Authorized redirect URIs")
    check("PKCE downgrade is refused",
          "error=invalid_request" in authorize(code_challenge_method="plain"))
    check("an unlisted resource is refused",
          "error=invalid_target" in authorize(resource="https://elsewhere.example/mcp"))
    check("an unregistered redirect_uri is refused, and not redirected to",
          authorize(redirect_uri="https://evil.example/cb") == "")
    status, _ = post(f"{issuer}/token", {
        "grant_type": "authorization_code", "code": "not-a-real-code",
        "client_id": client_id, "redirect_uri": "http://127.0.0.1:47821/cb",
        "code_verifier": verifier}, form=True)
    check("a bogus authorization code is refused", status == 400)

    return failures


def login(issuer: str, resource: str) -> int:
    """The full flow, with a real human at the Google step."""
    port = 47821
    redirect = f"http://127.0.0.1:{port}/cb"
    status, client = post(f"{issuer}/register", {
        "client_name": "finchat-verify (local)", "redirect_uris": [redirect]})
    if status != 201:
        print(f"{BAD} registration failed: {client}")
        return 1
    client_id = client["client_id"]
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(8)

    caught: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            caught.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            done = "code" in caught
            self.wfile.write(
                b"<h2>You can close this tab.</h2>" if done
                else b"<h2>Authorization was refused. Check the terminal.</h2>")

        def log_message(self, *a):  # keep the console for our own output
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
              "code_challenge": challenge, "code_challenge_method": "S256",
              "state": state, "resource": resource, "scope": "mcp:tools"}
    url = f"{issuer}/authorize?{urllib.parse.urlencode(params)}"
    print(f"\n  Opening a browser for the Google sign-in step.\n  {url}\n")
    webbrowser.open(url)

    for _ in range(120):
        if caught:
            break
        threading.Event().wait(1)
    server.server_close()

    if "code" not in caught:
        print(f"{BAD} no authorization code: {caught or 'timed out'}")
        return 1
    if caught.get("state", [""])[0] != state:
        print(f"{BAD} state did not round-trip — stopping")
        return 1

    status, token = post(f"{issuer}/token", {
        "grant_type": "authorization_code", "code": caught["code"][0],
        "client_id": client_id, "redirect_uri": redirect,
        "code_verifier": verifier}, form=True)
    if status != 200:
        print(f"{BAD} token exchange failed ({status}): {token}")
        return 1

    import jwt  # only needed on this path

    claims = jwt.decode(token["access_token"], options={"verify_signature": False},
                        audience=resource)
    print(f"{OK} token issued for {claims.get('email')}")
    print(f"       aud {claims.get('aud')}")
    print(f"       exp in {claims['exp'] - claims['iat']}s · scope {claims.get('scope')}")
    print(f"       refresh token present: {bool(token.get('refresh_token'))}")

    status, again = post(f"{issuer}/token", {
        "grant_type": "authorization_code", "code": caught["code"][0],
        "client_id": client_id, "redirect_uri": redirect,
        "code_verifier": verifier}, form=True)
    print(f"{OK if status == 400 else BAD} the code cannot be replayed")
    return 0 if status == 400 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="dev")
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--project", default="strongsville-city-schools")
    ap.add_argument("--login", action="store_true",
                    help="complete the real flow, opening a browser for Google sign-in")
    args = ap.parse_args()

    def url(service: str) -> str:
        return gcloud("run", "services", "describe", f"finchat-{args.env}-{service}",
                      "--region", args.region, "--project", args.project,
                      "--format=value(status.url)")

    issuer, mcp = url("mcp-auth"), url("mcp")
    if not issuer:
        print(f"finchat-{args.env}-mcp-auth is not deployed", file=sys.stderr)
        return 2
    resource = f"{mcp}/mcp"
    print(f"\nMCP OAuth proxy — {args.env}\n  issuer   {issuer}\n  resource {resource}\n")

    failed = checks(issuer, resource)
    if args.login:
        failed += login(issuer, resource)
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
