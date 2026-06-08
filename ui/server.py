"""
FinChat UI — backend-for-frontend (BFF) on Cloud Run.

- Serves the single-page app (static index.html).
- Proxies /api/loan/* and /api/txn/* to the backend Cloud Run services so the
  browser never holds backend URLs and CORS is avoided.
- Simulates login personas (customer / employee / admin): the persona is read
  from the X-Persona header and, for employee actions, injected as X-Approver
  on the upstream call. NO production IdP — role simulation for demo only.
- If a backend URL is unset, proxy returns 503 and the SPA falls back to its
  embedded demo data, so the UI runs standalone.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

LOAN_API_URL = os.getenv("LOAN_API_URL", "")
TXN_API_URL = os.getenv("TXN_API_URL", "")
AGENT_URL = os.getenv("AGENT_URL", "")
HERE = os.path.dirname(__file__)

app = FastAPI(title="FinChat UI BFF", version="1.0.0")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "backends": {
        "loan_api": bool(LOAN_API_URL), "txn_api": bool(TXN_API_URL), "agent": bool(AGENT_URL)}}


@app.get("/api/config")
def config():
    """Personas + which backends are live (drives demo-fallback in the SPA)."""
    return {
        "personas": [
            {"id": "customer", "label": "Customer (Jeremy)", "views": ["customer"]},
            {"id": "employee", "label": "Loan Officer", "views": ["employee"]},
            {"id": "admin", "label": "Platform Admin", "views": ["admin"]},
        ],
        "live": {"loan_api": bool(LOAN_API_URL), "txn_api": bool(TXN_API_URL), "agent": bool(AGENT_URL)},
    }


import time

_token_cache: dict[str, tuple[str, float]] = {}  # audience -> (token, expiry_epoch)


def _id_token(audience: str):
    """Mint (and cache) a Google OIDC id-token so the BFF can call PRIVATE Cloud Run
    backends. The BFF runs as a service account with run.invoker on the targets.
    Harmless for public services (they ignore the header). No-op locally (http)."""
    if not audience.startswith("https://"):
        return None
    now = time.time()
    cached = _token_cache.get(audience)
    if cached and cached[1] - 60 > now:
        return cached[0]
    try:
        from google.auth.transport.requests import Request as GReq
        import google.oauth2.id_token as idt
        tok = idt.fetch_id_token(GReq(), audience)
        _token_cache[audience] = (tok, now + 3000)  # tokens last ~1h; cache 50m
        return tok
    except Exception:
        return None


async def _proxy(base: str, path: str, request: Request) -> Response:
    if not base:
        return JSONResponse({"error": "backend not configured", "demo": True}, status_code=503)
    import httpx
    url = f"{base}/{path}"
    # Persona -> approver identity for employee write actions (role simulation).
    persona = request.headers.get("X-Persona", "customer")
    headers = {"content-type": request.headers.get("content-type", "application/json")}
    if persona == "employee":
        headers["X-Approver"] = request.headers.get("X-Approver", "loan-officer@datadinosaur.com")
    # OIDC: authenticate to private Cloud Run backends (audience = service base URL).
    token = _id_token(base)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = await request.body()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.request(request.method, url, params=request.query_params,
                                 content=body or None, headers=headers)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


@app.api_route("/api/loan/{path:path}", methods=["GET", "POST"])
async def loan_proxy(path: str, request: Request):
    return await _proxy(LOAN_API_URL, path, request)


@app.api_route("/api/txn/{path:path}", methods=["GET", "POST"])
async def txn_proxy(path: str, request: Request):
    return await _proxy(TXN_API_URL, path, request)


@app.api_route("/api/agent/{path:path}", methods=["GET", "POST"])
async def agent_proxy(path: str, request: Request):
    """Agent path with Model Armor screening on prompt (in) and response (out)."""
    import armor
    body = await request.body()
    if body:
        ok, reason = await armor.screen_prompt(body.decode("utf-8", "replace"))
        if not ok:
            return JSONResponse(
                {"error": "Your message was blocked by safety screening.", "reason": reason},
                status_code=400)
    resp = await _proxy(AGENT_URL, path, request)
    # Screen the model response before returning it to the user.
    try:
        ok, reason = await armor.screen_response(resp.body.decode("utf-8", "replace"))
        if not ok:
            return JSONResponse(
                {"error": "The response was withheld by safety screening.", "reason": reason},
                status_code=502)
    except Exception:
        pass
    return resp


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8082")))
