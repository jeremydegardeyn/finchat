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
EVAL_DATASET = os.getenv("EVAL_DATASET", "")    # live-eval: conversation_log capture
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")  # Gemini for intent routing
ANALYST_READY = bool(GCP_PROJECT and (SILVER_DATASET or GOLD_DATASET or LOANS_DATASET))

# --- Identity-resolved personas (Google Sign-In, ADR-0016) -------------------
# When configured, the persona is resolved from a VERIFIED Google identity (the SPA
# sends the GIS ID token as X-User-Token) instead of the demo dropdown. Customers
# stay anonymous; staff personas require sign-in and are enforced per-route.
OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")


def _emails(var: str) -> set[str]:
    return {e.strip().lower() for e in os.getenv(var, "").split(",") if e.strip()}


APPROVER_EMAILS = _emails("APPROVER_EMAILS")  # -> employee (Loan Officer) view
ANALYST_EMAILS = _emails("ANALYST_EMAILS")    # -> analyst view
ADMIN_EMAILS = _emails("ADMIN_EMAILS")        # -> admin view
PERSONA_LABELS = {"employee": "Loan Approver", "analyst": "Analyst",
                  "admin": "Platform Admin", "customer": "Customer"}


def _auth_enabled() -> bool:
    return bool(OAUTH_CLIENT_ID and (APPROVER_EMAILS or ANALYST_EMAILS or ADMIN_EMAILS))


def _persona_for(email: str) -> str | None:
    if email in APPROVER_EMAILS:
        return "employee"
    if email in ANALYST_EMAILS:
        return "analyst"
    if email in ADMIN_EMAILS:
        return "admin"
    return None

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
        # Identity-resolved personas: when enabled the SPA shows Google Sign-In
        # instead of the persona dropdown (client_id is public by design).
        "auth": {"enabled": _auth_enabled(), "client_id": OAUTH_CLIENT_ID},
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


_http = None  # shared pooled HTTP client (keep-alive: saves a TLS handshake per hop)


def _client():
    global _http
    if _http is None:
        import httpx
        _http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
    return _http


_user_cache: dict[str, dict] = {}  # GIS id-token -> {email, persona, exp}


def _verify_user(request: Request) -> dict | None:
    """Verify the Google Sign-In ID token (X-User-Token header) — signature,
    audience (our OAuth client), issuer, expiry — and resolve the persona from the
    verified email. Returns {email, persona, exp} or None."""
    tok = request.headers.get("X-User-Token", "")
    if not (tok and OAUTH_CLIENT_ID):
        return None
    now = time.time()
    u = _user_cache.get(tok)
    if u and u["exp"] - 30 > now:
        return u
    try:
        from google.oauth2 import id_token as gid
        from google.auth.transport.requests import Request as GReq
        info = gid.verify_oauth2_token(tok, GReq(), OAUTH_CLIENT_ID)
        if not info.get("email_verified"):
            return None
        email = (info.get("email") or "").lower()
        u = {"email": email, "persona": _persona_for(email),
             "exp": float(info.get("exp", now + 300))}
        if len(_user_cache) > 500:
            _user_cache.clear()
        _user_cache[tok] = u
        return u
    except Exception:
        return None


def _require(request: Request, persona: str):
    """Return a 403 JSONResponse if auth is enabled and the caller's VERIFIED
    persona isn't `persona`; None when allowed (or auth not configured)."""
    if not _auth_enabled():
        return None
    u = _verify_user(request)
    if not u or u.get("persona") != persona:
        who = PERSONA_LABELS.get(persona, persona)
        return JSONResponse(
            {"error": f"{who} sign-in required (your session may have expired — sign in again)"},
            status_code=403)
    return None


@app.get("/api/me")
def me(request: Request):
    """Resolve the signed-in user's persona from their verified Google identity."""
    if not _auth_enabled():
        return {"auth_enabled": False}
    u = _verify_user(request)
    if not u:
        return {"auth_enabled": True, "signed_in": False}
    p = u["persona"] or "customer"
    return {"auth_enabled": True, "signed_in": True, "email": u["email"],
            "persona": p, "persona_label": PERSONA_LABELS.get(p, "Customer")}


async def _log_eval(persona: str, channel: str, question: str, answer: str, context=None):
    """Best-effort capture of a conversation turn for live evaluation. Awaited (in a
    worker thread) WITHIN the request — Cloud Run throttles CPU once the response is
    sent, so a fire-and-forget background thread would never run. Never raises."""
    if not (GCP_PROJECT and EVAL_DATASET and (question or "").strip()):
        return

    def _do():
        try:
            import uuid as _uuid
            import json as _json
            from datetime import datetime, timezone
            from google.cloud import bigquery
            row = {"conversation_id": str(_uuid.uuid4()),
                   "ts": datetime.now(timezone.utc).isoformat(),
                   "persona": persona, "channel": channel,
                   "question": (question or "")[:4000], "answer": (answer or "")[:8000],
                   "context": (_json.dumps(context)[:8000] if context else None)}
            bigquery.Client(project=GCP_PROJECT).insert_rows_json(
                f"{GCP_PROJECT}.{EVAL_DATASET}.conversation_log", [row])
        except Exception:
            pass

    try:
        import asyncio
        await asyncio.to_thread(_do)
    except Exception:
        pass


async def _proxy(base: str, path: str, request: Request,
                 extra_headers: dict | None = None) -> Response:
    if not base:
        return JSONResponse({"error": "backend not configured", "demo": True}, status_code=503)
    url = f"{base}/{path}"
    headers = {"content-type": request.headers.get("content-type", "application/json")}
    # Role SIMULATION fallback only (no OAuth configured): trust the client headers.
    # With auth enabled, privileged routes set X-Approver to the VERIFIED identity
    # via extra_headers (see loan_proxy) and client-sent values are ignored.
    if not _auth_enabled() and request.headers.get("X-Persona", "customer") == "employee":
        headers["X-Approver"] = request.headers.get("X-Approver", "loan-officer@datadinosaur.com")
    # OIDC: authenticate to private Cloud Run backends (audience = service base URL).
    token = _id_token(base)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    body = await request.body()
    r = await _client().request(request.method, url, params=request.query_params,
                                content=body or None, headers=headers,
                                timeout=90.0)  # agent cold-start + Gemini latency
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


@app.api_route("/api/loan/{path:path}", methods=["GET", "POST"])
async def loan_proxy(path: str, request: Request):
    """Customer routes (create, status, notify) stay open. Loan-OPS routes — the
    review queue, audit trail, and decisions — require the verified approver, and
    the decision's X-Approver becomes the authenticated email (immutable audit)."""
    p = path.rstrip("/")
    privileged = (p.endswith("/decision") or p.endswith("/audit")
                  or (p == "v1/loans" and request.method == "GET"))
    extra = None
    if privileged and _auth_enabled():
        deny = _require(request, "employee")
        if deny:
            return deny
        u = _verify_user(request)
        extra = {"X-Approver": u["email"]}  # verified identity -> append-only audit
    return await _proxy(LOAN_API_URL, path, request, extra_headers=extra)


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
    # Capture the turn for live eval (customer banking-assistant chats).
    try:
        import json as _json
        q = (_json.loads(body or b"{}") or {}).get("message", "")
        a = (_json.loads(resp.body or b"{}") or {}).get("response", "")
        if q:
            await _log_eval(request.headers.get("X-Persona", "customer"), "agent", q, a)
    except Exception:
        pass
    return resp


# ============================= Analyst persona ==============================
# Catalog discovery + Google Conversational Analytics, for the "Employee (Analyst)"
# persona only (the SPA exposes these in the Analyst view; the customer agent no
# longer carries the catalog-discovery tool).

def _is_finchat(*vals) -> bool:
    # BigQuery entries use finchat_<dataset>; data-product/glossary use finchat-<env>.
    return any(("finchat_" in v or "finchat-" in v) for v in vals if v)


@app.get("/api/catalog/search")
def catalog_search(request: Request, q: str = "", raw: int = 0):
    """Discover Dataplex catalog assets by free-text description. Returns matching
    entries with their governed aspects (data-product, governance, data-contract,
    operational) so the analyst can see ownership, PII class, contract, and DQ."""
    deny = _require(request, "analyst")
    if deny:
        return deny
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
        matches, seen, rawlist = [], set(), []
        for res in client.search_entries(request={"name": scope, "query": q, "page_size": 25}):
            entry = getattr(res, "dataplex_entry", None)
            if not entry:
                continue
            name = getattr(entry, "name", "")
            resource = res.linked_resource or getattr(entry, "fully_qualified_name", "") or ""
            etype = (getattr(entry, "entry_type", "") or "").split("/")[-1]
            rawlist.append({"type": etype, "name": name[-70:], "resource": resource[:70]})
            is_term = etype in ("glossary-term", "glossary-category")
            # FinChat assets only: keep finchat_* / finchat-* entries (drops billing-export
            # and other auto-harvested project tables). Match name OR resource, _ OR -.
            if not is_term and not _is_finchat(resource, name):
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
            if len(matches) >= 20:
                break
        # Surface governed data products (entries carrying FinChat aspects) first; keep
        # relevance order within each group. Glossary terms next, raw tables/datasets last.
        matches.sort(key=lambda m: (0 if m["aspects"] else (1 if m["entry_type"] in
                     ("glossary-term", "glossary-category") else 2)))
        matches = matches[:8]
        if raw:  # diagnostic: what the BFF SA's search actually returns, pre-filter
            return {"matches": matches, "raw": rawlist}
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
    "Never expose customer names, email addresses, or account numbers; identify rows by "
    "customer_id, account_id, and segment only. (Names/emails are also blocked by "
    "column-level security; account_number shares the financial tag with amount, so this "
    "instruction is the control until the taxonomy splits identifiers from values.)"
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
    columns, rows, followups, vega}."""
    answer, followups, sql, rows, cols, vega = [], [], None, [], [], None
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
        if "chart" in sm:  # Vega-Lite spec (self-contained, inline data) for the chart
            vc = (sm["chart"].get("result") or {}).get("vegaConfig")
            if vc:
                vega = vc
    return {"answer": "\n\n".join(a for a in answer if a).strip() or "(no answer returned)",
            "sql": sql, "columns": cols, "rows": rows, "followups": followups[:3], "vega": vega}


async def _run_ca(q: str) -> dict:
    """Conversational Analytics over the data products (graph-grounded)."""
    tables = _analyst_tables()
    if not (ANALYST_READY and tables):
        return {"mode": "analytics", "error": "analytics not configured", "demo": True}
    try:
        token = _access_token()
    except Exception:
        return {"mode": "analytics", "error": "no credentials", "demo": True}
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
    r = await _client().post(url, json=payload, headers={"Authorization": f"Bearer {token}"},
                             timeout=150.0)
    if r.status_code >= 400:
        return {"mode": "analytics", "error": f"analytics error {r.status_code}", "detail": r.text[:400]}
    try:
        import json as _json
        msgs = r.json() if r.text.lstrip().startswith("[") else \
            [_json.loads(li) for li in r.text.splitlines() if li.strip()]
    except Exception:
        return {"mode": "analytics", "error": "could not parse analytics response"}
    return {"mode": "analytics", **_parse_ca(msgs)}


async def _run_kb(q: str) -> dict:
    """Knowledge-base RAG via the banking agent's search_knowledge_base tool."""
    if not AGENT_URL:
        return {"mode": "kb", "error": "knowledge base not configured", "demo": True}
    headers = {"content-type": "application/json"}
    token = _id_token(AGENT_URL)  # OIDC to the private agent
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = await _client().post(f"{AGENT_URL}/chat", headers=headers, json={
            "message": q, "user_id": "analyst", "session_id": "analyst-kb"}, timeout=90.0)
        data = r.json()
    except Exception as e:
        return {"mode": "kb", "error": f"knowledge base unavailable: {type(e).__name__}"}
    return {"mode": "kb", "answer": data.get("response") or "(no answer)"}


_KB_WORDS = ("fee", "polic", "hour", "branch", "atm", " open", "close", "term", "condition",
             "privacy", "eligib", "require", "interest", "rate", "offer", "document", "contact",
             "support", "location", "how do i", "what is a", "limit", "disclosure")
_AN_WORDS = ("how many", "count", "number of", "total", "sum", "average", "avg", "median", "top ",
             " most ", "least", "list ", "per segment", "by segment", "per customer", "distribution",
             "breakdown", "customers with", "which customer", "trend", "over time", "compare",
             "percentage", "ratio", "largest", "smallest", "highest", "lowest", "how much")


def _heuristic_intent(q: str) -> str:
    ql = q.lower()
    kb = sum(w in ql for w in _KB_WORDS)
    an = sum(w in ql for w in _AN_WORDS)
    return "kb" if kb > an else "analytics"


async def _classify_intent(q: str) -> str:
    """Decide ANALYTICS vs KB via Gemini (Vertex), falling back to a keyword
    heuristic if the model isn't reachable (e.g. SA lacks aiplatform.user)."""
    prompt = (
        "You route a bank analyst's question to one of two tools. Reply with ONE word.\n"
        "ANALYTICS = a quantitative question about the bank's DATA (counts, sums, averages, "
        "lists, per-segment/per-customer metrics over transactions, accounts, customers, loans, "
        "overdrafts).\n"
        "KB = a question answerable from the bank's POLICY/PRODUCT DOCUMENTS (fees, policies, "
        "branch hours, terms, eligibility, rates offered, how-to).\n"
        f"Question: {q}\nAnswer (ANALYTICS or KB):")
    try:
        token = _access_token()
        url = (f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{GCP_PROJECT}"
               f"/locations/{VERTEX_LOCATION}/publishers/google/models/gemini-2.5-flash:generateContent")
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 4}}
        r = await _client().post(url, json=body, headers={"Authorization": f"Bearer {token}"},
                                 timeout=15.0)
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].upper()
        if "KB" in txt and "ANALYTIC" not in txt:
            return "kb"
        if "ANALYTIC" in txt:
            return "analytics"
    except Exception:
        pass
    return _heuristic_intent(q)


@app.post("/api/analyst/chat")
async def analyst_chat(request: Request):
    """Force Conversational Analytics (kept for direct callers)."""
    deny = _require(request, "analyst")
    if deny:
        return deny
    body = await request.json()
    q = (body.get("message") or "").strip()
    if not q:
        return JSONResponse({"error": "empty message"}, status_code=400)
    return await _run_ca(q)


@app.post("/api/analyst/ask")
async def analyst_ask(request: Request):
    """One analyst assistant: classify the question (Gemini, heuristic fallback) and
    route to Conversational Analytics OR the Knowledge Base RAG accordingly. Returns
    {mode, answer, ...} so the UI shows which tool answered."""
    deny = _require(request, "analyst")
    if deny:
        return deny
    body = await request.json()
    q = (body.get("message") or "").strip()
    if not q:
        return JSONResponse({"error": "empty message"}, status_code=400)
    mode = await _classify_intent(q)
    res = await (_run_kb(q) if mode == "kb" else _run_ca(q))
    # Capture for live eval; for analytics the generated SQL + rows are the grounding context.
    ctx = None
    if res.get("mode") == "analytics":
        ctx = {"sql": res.get("sql"), "rows": (res.get("rows") or [])[:10]}
    await _log_eval("analyst", res.get("mode", mode), q, res.get("answer", ""), ctx)
    return res


@app.get("/api/eval")
def eval_report(request: Request):
    """Drives the Admin -> Evaluations card. Prefers LIVE rolling metrics from scored
    production conversations (BigQuery eval_summary); falls back to the offline,
    CI-gated report baked into the image."""
    deny = _require(request, "admin")
    if deny:
        return deny
    import json
    # 1) Live: rolling 7-day metrics over real, LLM-judged conversations.
    if GCP_PROJECT and EVAL_DATASET:
        try:
            from google.cloud import bigquery
            rows = list(bigquery.Client(project=GCP_PROJECT).query(
                f"SELECT * FROM `{GCP_PROJECT}.{EVAL_DATASET}.eval_summary`").result())
            if rows and rows[0]["n"]:
                d = dict(rows[0])
                ts = d.get("last_scored_at")
                return {"available": True, "live": True, "n": d["n"],
                        "generated_at": ts.isoformat() if ts else None,
                        "metrics": [
                            {"label": "Grounding", "value": d["grounding_accuracy"]},
                            {"label": "Hallucination", "value": d["hallucination_rate"]},
                            {"label": "Instruction-following", "value": d["instruction_following"]},
                            {"label": "Safety", "value": d["safety"]}]}
        except Exception:
            pass
    # 2) Offline: CI-gated report baked into the image.
    try:
        with open(os.path.join(HERE, "eval_report.json")) as f:
            d = json.load(f)
        s = d.get("summary", {})
        return {"available": True, "live": False, "generated_at": d.get("generated_at"),
                "n_txn": d.get("transaction_agent", {}).get("n"),
                "n_loan": d.get("loan_recommendations", {}).get("n"),
                "metrics": [
                    {"label": "Grounding accuracy", "value": s.get("grounding_accuracy")},
                    {"label": "Hallucination rate", "value": s.get("hallucination_rate")},
                    {"label": "Tool utilization", "value": s.get("tool_utilization")},
                    {"label": "Approval rec. accuracy", "value": s.get("approval_recommendation_accuracy")}]}
    except Exception:
        return {"available": False}


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8082")))
