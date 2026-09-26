"""
HTTP server for the FinChat Banking Assistant — runs the ADK agent on Cloud Run
(true scale-to-zero, ~$0 idle; ADR-0004 fallback to Agent Engine).

POST /chat   {message, user_id?, session_id?, include_trajectory?}
             -> {response, session_id, model_requested, model_served}
             -> plus {trajectory, tool_outputs, intermediate_events, gateway_path}
                when include_trajectory is set
POST /search {query}                          -> {results: [{title, category, content, retriever}]}
Uses an in-memory session store (resets on cold start — fine for the sandbox; use
VertexAiSessionService / a DB for durable multi-turn at enterprise scale).

`/search` exposes retrieval *without* an agent turn, for callers that want passages to
ground on rather than another model's prose — the MCP server (ADR-0028) is the first.
Wrapping retrieval in `/chat` for those callers would stack two models, bill the
gateway twice, and hand the caller a summary it cannot cite or check for a miss.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from pydantic import BaseModel
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import root_agent
from trajectory import TurnTrace

# Distributed tracing (ADR-0035). See the identical bootstrap in ui/server.py for why it
# is seven lines rather than an import: each service has its own Docker build context.
try:
    import tracing                                        # image: copied beside us
except ImportError:                                       # checkout: walk up to the root
    import sys as _sys
    _d = os.path.dirname(os.path.abspath(__file__))
    while _d != os.path.dirname(_d):
        if os.path.isdir(os.path.join(_d, "observability")):
            _sys.path.insert(0, os.path.join(_d, "observability")); break
        _d = os.path.dirname(_d)
    import tracing

# THE point of this increment on this service. ADK instruments itself — it creates spans
# named invoke_agent / call_llm / execute_tool under the tracer `gcp.vertex.agent`, with
# the invocation id, session id, model and token counts on them — and then drops every one
# of them into the no-op default TracerProvider, because nothing here ever configured a
# real one. Vertex AI Agent Engine's "native tracing", the capability ADR-0010 recorded as
# given up by moving to Cloud Run, is this configuration and nothing more. So the debt is
# paid by one call, at a cost of $0/month instead of the ~$75-110/mo per engine that
# Agent Engine's compute baseline would cost to obtain the same spans.
#
# The spans ADK builds carry `gcp.vertex.agent.llm_request` and `llm_response` — the
# serialized prompt and answer. tracing.RedactingExporter is what stands between those and
# Cloud Trace, and it is the reason this is not simply ADK's own get_gcp_exporters().
tracing.init("agent")

APP_NAME = "finchat-banking-assistant"

app = FastAPI(title=APP_NAME, version="1.0.0")


@app.middleware("http")
async def _trace_requests(request: Request, call_next):
    """Continue the BFF's trace rather than starting a new one.

    Without this the agent's spans — the ADK ones included — land on a trace of their own,
    and the interesting question ("the turn took nine seconds; was it the agent, its tools,
    or the model?") stays unanswerable because the BFF's half and the agent's half are two
    unrelated traces.
    """
    if not tracing.ENABLED:
        return await call_next(request)
    with tracing.server_span(f"{request.method} {request.url.path}",
                             request.headers, route=request.url.path):
        resp = await call_next(request)
        tracing.set_attrs(http_status=resp.status_code)
        return resp
_runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=InMemorySessionService(),
    auto_create_session=True,
)


class ChatReq(BaseModel):
    message: str
    user_id: str = "demo"
    session_id: str = "default"
    # Evaluation surface, off by default (eval/pipelines/vertex_eval.py). The customer
    # path proxies this response body through the BFF to the browser verbatim, and the
    # tool trace carries account ids, raw tool output and the agent's internal step
    # sequence. None of that belongs in a customer reply, so a caller that wants the
    # trace asks for it and the default turn stays byte-identical.
    include_trajectory: bool = False


@app.get("/healthz")
def healthz():
    return {"status": "ok", "agent": root_agent.name if root_agent else None}


@app.post("/chat")
async def chat(req: ChatReq):
    """One agent turn, optionally reporting the tool trajectory it took to get there.

    The trajectory is not decoration: a final answer alone cannot be scored for tool
    selection or grounded against the data the tools actually returned. Harvesting it
    here — from the events the Runner already emits — is what lets the managed
    evaluation service score this agent on anything beyond its prose.
    """
    msg = types.Content(role="user", parts=[types.Part(text=req.message)])
    # The folding logic lives in trajectory.py so it is testable without ADK, a model or
    # an agent — none of which a `Runner` built at import time lets a test avoid.
    trace = TurnTrace(collect_trajectory=req.include_trajectory)
    async for event in _runner.run_async(
        user_id=req.user_id, session_id=req.session_id, new_message=msg
    ):
        trace.add(event)

    # `path` is the gateway_llm routing decision (governed vs direct-to-Vertex, ADR-0024):
    # a closed vocabulary, so it is a label rather than content.
    # Which tools ran is NOT set here: the trajectory is only harvested under
    # include_trajectory, so it would be empty on every real turn. ADK's own
    # execute_tool spans name them on every turn, which is the better source anyway.
    tracing.set_attrs(model_requested=trace.model_requested,
                      model_served=trace.model_served,
                      gateway_outcome=trace.path)

    return trace.payload(req.session_id)


class SearchReq(BaseModel):
    query: str


@app.post("/search")
def search(req: SearchReq):
    """Retrieval only: the same hybrid + rerank path the agent's tool uses.

    Deliberately the *tool function*, not a re-implementation. One retrieval
    pipeline means the agent and every other consumer see the same ranking, and
    the `retriever` field keeps it observable which arm won (docs/21).
    """
    import tools

    return {"results": tools.search_knowledge_base(req.query)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8083")))
