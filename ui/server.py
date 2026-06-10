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

# Analyst persona: Knowledge Catalog discovery + Conversational Analytics (Gemini
# Data Analytics). The UI BFF SA (txn-api) holds geminidataanalytics.locations.chat
# + BigQuery read/job, so analyst features run under it.
GCP_PROJECT = os.getenv("GCP_PROJECT", "")
CA_LOCATION = os.getenv("CA_LOCATION", "global")  # Conversational Analytics location
SILVER_DATASET = os.getenv("SILVER_DATASET", "")
GOLD_DATASET = os.getenv("GOLD_DATASET", "")
LOANS_DATASET = os.getenv("LOANS_DATASET", "")
GRAPH_DATASET = os.getenv("GRAPH_DATASET", "")  # knowledge graph (customer_360, kg_*)
ANALYST_READY = bool(GCP_PROJECT and (SILVER_DATASET or GOLD_DATASET or LOANS_DATASET))

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
            {"id": "employee", "label": "Employee (Loan Officer)", "views": ["employee"]},
            {"id": "analyst", "label": "Employee (Analyst)", "views": ["analyst"]},
            {"id": "admin", "label": "Platform Admin", "views": ["admin"]},
        ],
        "live": {"loan_api": bool(LOAN_API_URL), "txn_api": bool(TXN_API_URL),
                 "agent": bool(AGENT_URL), "analyst": ANALYST_READY},
    }


import time

_token_cache: dict[str, tuple[str, float]] = {}  # audience -> (token, expiry_epoch)


def _id_token(audience: str):
    """Mint (and cache) a Google OIDC id-token so the BFF can call PRIVATE Cloud Run
    backends. Uses the metadata server's identity endpoint (canonical + reliable for
    Cloud Run service-to-service auth). The BFF SA holds run.invoker on the targets.
    Harmless for public services; no-op locally (no metadata server)."""
    if not audience.startswith("https://"):
        return None
    now = time.time()
    cached = _token_cache.get(audience)
    if cached and cached[1] - 60 > now:
        return cached[0]
    try:
        tok = _mint_token(audience)
        _token_cache[audience] = (tok, now + 3000)  # tokens last ~1h; cache 50m
        return tok
    except Exception:
        return None


def _mint_token(audience: str) -> str:
    """Mint a Cloud Run id-token for `audience` via the metadata identity endpoint."""
    from google.auth import compute_engine
    from google.auth.transport.requests import Request as GReq
    creds = compute_engine.IDTokenCredentials(
        GReq(), target_audience=audience, use_metadata_identity_endpoint=True)
    creds.refresh(GReq())
    return creds.token


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
    async with httpx.AsyncClient(timeout=90.0) as client:  # agent cold-start + Gemini latency
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


# ============================= Analyst persona ==============================
# Catalog discovery + Google Conversational Analytics, for the "Employee (Analyst)"
# persona only (the SPA exposes these in the Analyst view; the customer agent no
# longer carries the catalog-discovery tool).

@app.get("/api/catalog/search")
def catalog_search(q: str = ""):
    """Discover Dataplex catalog assets by free-text description. Returns matching
    entries with their governed aspects (data-product, governance, data-contract,
    operational) so the analyst can see ownership, PII class, contract, and DQ."""
    q = (q or "").strip()
    if not q:
        return {"matches": []}
    if not GCP_PROJECT:
        return {"matches": [], "error": "catalog not configured"}
    try:
        from google.cloud import dataplex_v1
        client = dataplex_v1.CatalogServiceClient()
        scope = f"projects/{GCP_PROJECT}/locations/global"
        env = SILVER_DATASET.rsplit("_", 1)[-1] if "_" in SILVER_DATASET else ""  # e.g. "prod"
        matches, seen = [], set()
        for res in client.search_entries(request={"name": scope, "query": q, "page_size": 25}):
            entry = getattr(res, "dataplex_entry", None)
            if not entry:
                continue
            name = getattr(entry, "name", "")
            resource = res.linked_resource or ""
            etype = (getattr(entry, "entry_type", "") or "").split("/")[-1]
            is_term = etype in ("glossary-term", "glossary-category")
            # FinChat assets only: BigQuery entries must be in a finchat_ dataset
            # (drops billing-export and other auto-harvested project tables). Glossary
            # terms/data-product entries are kept.
            if not is_term and "finchat_" not in resource and "finchat-" not in name:
                continue
            # Scope to this env (drop other envs' duplicate tables); keep env-less terms.
            if env and env not in resource and env not in name and not is_term:
                continue
            # Search snippets omit aspects — fetch the full entry to read them.
            aspects = {}
            try:
                full = client.get_entry(request={"name": name, "view": "ALL"})
                for k, asp in dict(full.aspects).items():
                    short = k.split(".")[-1]
                    if short.startswith("finchat-"):
                        # ...finchat-prod-data-contract -> "data-contract".
                        # asp.data is a proto-plus MapComposite (dict-like), not a
                        # protobuf Message — dict() it, don't use MessageToDict.
                        aspects[short.split("-", 2)[-1]] = {kk: str(vv) for kk, vv in dict(asp.data).items()}
            except Exception:
                pass
            src = getattr(entry, "entry_source", None)
            disp = (getattr(src, "display_name", "") or name.split("/")[-1]) or resource
            dk = (disp, etype)
            if dk in seen:
                continue
            seen.add(dk)
            matches.append({"name": disp, "resource": resource, "entry_type": etype, "aspects": aspects})
            if len(matches) >= 8:
                break
        return {"matches": matches}
    except Exception as e:
        return {"matches": [], "error": f"{type(e).__name__}: {e}"}


def _analyst_tables() -> list[dict]:
    """BigQuery tables exposed to Conversational Analytics. Includes the `account`
    bridge (so transaction->customer joins resolve) and the pre-joined knowledge-
    graph `customer_360` rollup. The KB vector table is excluded."""
    t = []
    if SILVER_DATASET:
        t += [{"projectId": GCP_PROJECT, "datasetId": SILVER_DATASET, "tableId": "transaction"},
              {"projectId": GCP_PROJECT, "datasetId": SILVER_DATASET, "tableId": "account"},
              {"projectId": GCP_PROJECT, "datasetId": SILVER_DATASET, "tableId": "customer"}]
    if GOLD_DATASET:
        t.append({"projectId": GCP_PROJECT, "datasetId": GOLD_DATASET, "tableId": "overdraft_history"})
    if LOANS_DATASET:
        t.append({"projectId": GCP_PROJECT, "datasetId": LOANS_DATASET, "tableId": "loan_status"})
    if GRAPH_DATASET:  # knowledge graph: pre-joined per-customer rollup + relationships
        t += [{"projectId": GCP_PROJECT, "datasetId": GRAPH_DATASET, "tableId": "customer_360"},
              {"projectId": GCP_PROJECT, "datasetId": GRAPH_DATASET, "tableId": "kg_relationships"}]
    return t


# Knowledge-graph join model — teaches Conversational Analytics the correct joins
# (it previously couldn't link transaction->customer because transactions carry
# only account_id). Mirrors finchat_graph_<env>.kg_relationships.
_ANALYST_SYSTEM_INSTRUCTION = (
    "You are a banking data analyst assistant for FinChat. Answer questions over the "
    "provided BigQuery tables. The data model is a graph — ALWAYS join using these keys:\n"
    "- account.customer_id = customer.customer_id  (an Account BELONGS_TO a Customer)\n"
    "- transaction.account_id = account.account_id (a Transaction OCCURS_ON an Account; "
    "transactions have NO customer_id, so to attribute a transaction to a customer join "
    "transaction -> account -> customer)\n"
    "- overdraft_history.account_id = account.account_id\n"
    "- loan_status / loan_request relate to an account via account_id\n"
    "For per-customer questions, PREFER the pre-joined `customer_360` view (one row per "
    "customer with account/transaction/overdraft/loan rollups) instead of joining manually. "
    "Transaction amounts: DEPOSIT is cash in (positive); WITHDRAWAL and FEE reduce balance. "
    "Never expose customer names or email addresses; use customer_id and segment."
)


def _access_token() -> str:
    """OAuth access token for the BFF SA (cloud-platform scope) to call Google APIs."""
    from google.auth import default
    from google.auth.transport.requests import Request as GReq
    creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(GReq())
    return creds.token


def _parse_ca(messages: list) -> dict:
    """Reduce a Conversational Analytics streamed message array to {answer, sql,
    columns, rows, followups}."""
    answer, followups, sql, rows, cols = [], [], None, [], []
    for m in messages if isinstance(messages, list) else []:
        sm = m.get("systemMessage", {}) if isinstance(m, dict) else {}
        if "text" in sm:
            t = sm["text"]; tt = t.get("textType", "")
            if tt == "FINAL_RESPONSE":
                answer.append(" ".join(t.get("parts", [])))
            elif tt == "FOLLOWUP_QUESTIONS":
                followups.extend(t.get("parts", []))
        if "data" in sm:
            d = sm["data"]
            if d.get("generatedSql"):
                sql = d["generatedSql"]
            if "result" in d:
                data = d["result"].get("data", []) or []
                if data:
                    rows = data[:50]
                    cols = list(rows[0].keys())
    return {"answer": "\n\n".join(a for a in answer if a).strip() or "(no answer returned)",
            "sql": sql, "columns": cols, "rows": rows, "followups": followups[:3]}


@app.post("/api/analyst/chat")
async def analyst_chat(request: Request):
    """Natural-language analytics over the FinChat data products via Google's
    Conversational Analytics API (Gemini Data Analytics). Returns a grounded answer,
    the generated SQL, and the result rows."""
    body = await request.json()
    q = (body.get("message") or "").strip()
    if not q:
        return JSONResponse({"error": "empty message"}, status_code=400)
    tables = _analyst_tables()
    if not (ANALYST_READY and tables):
        return JSONResponse({"error": "analytics not configured", "demo": True}, status_code=503)
    try:
        token = _access_token()
    except Exception:
        return JSONResponse({"error": "no credentials", "demo": True}, status_code=503)
    import httpx
    url = (f"https://geminidataanalytics.googleapis.com/v1beta/projects/{GCP_PROJECT}"
           f"/locations/{CA_LOCATION}:chat")
    payload = {
        "parent": f"projects/{GCP_PROJECT}/locations/{CA_LOCATION}",
        "messages": [{"userMessage": {"text": q}}],
        "inline_context": {
            "system_instruction": _ANALYST_SYSTEM_INSTRUCTION,  # teaches the graph joins
            "datasource_references": {"bq": {"table_references": tables}},
        },
    }
    async with httpx.AsyncClient(timeout=150.0) as client:
        r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        return JSONResponse({"error": f"analytics error {r.status_code}",
                             "detail": r.text[:400]}, status_code=502)
    try:
        import json as _json
        msgs = r.json() if r.text.lstrip().startswith("[") else \
            [_json.loads(li) for li in r.text.splitlines() if li.strip()]
    except Exception:
        return JSONResponse({"error": "could not parse analytics response"}, status_code=502)
    return _parse_ca(msgs)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8082")))
