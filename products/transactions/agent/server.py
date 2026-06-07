"""
HTTP server for the FinChat Banking Assistant — runs the ADK agent on Cloud Run
(true scale-to-zero, ~$0 idle; ADR-0004 fallback to Agent Engine).

POST /chat {message, user_id?, session_id?} -> {response, session_id}
Uses an in-memory session store (resets on cold start — fine for the sandbox; use
VertexAiSessionService / a DB for durable multi-turn at enterprise scale).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import root_agent

APP_NAME = "finchat-banking-assistant"

app = FastAPI(title=APP_NAME, version="1.0.0")
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


@app.get("/healthz")
def healthz():
    return {"status": "ok", "agent": root_agent.name if root_agent else None}


@app.post("/chat")
async def chat(req: ChatReq):
    msg = types.Content(role="user", parts=[types.Part(text=req.message)])
    text = ""
    async for event in _runner.run_async(
        user_id=req.user_id, session_id=req.session_id, new_message=msg
    ):
        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
    return {"response": text, "session_id": req.session_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8083")))
