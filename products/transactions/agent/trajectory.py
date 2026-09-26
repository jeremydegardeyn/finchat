"""Accumulate one agent turn's ADK events into an answer plus its tool trajectory.

Why this is not inline in server.py
-----------------------------------
`server.py` builds an ADK `Runner` at import time, so nothing in it can be tested without
the framework, a model and an agent. This is the part with the actual logic — which parts
are tool calls, which are tool results, which event carries the serving version — so it
lives where a test can reach it with plain fakes. `test_trajectory.py` is that test.

What a turn looks like
----------------------
A tool-calling turn is several model calls, not one. The Runner emits an event carrying a
`functionCall`, then an event carrying the matching `functionResponse`, then (possibly
after more of the same) a final text event. All three matter to evaluation and only the
last one reaches the customer:

  * the calls are the trajectory — what tool-selection metrics score
  * the results are the grounding evidence — what a hallucination judge needs to
    compare the answer against
  * the final text is the answer
"""
from __future__ import annotations


class TurnTrace:
    """Collects a turn's answer, trajectory, tool output and serving version."""

    def __init__(self, *, collect_trajectory: bool = False):
        self.collect_trajectory = collect_trajectory
        self.text = ""
        self.calls: list[dict] = []
        self.outputs: list[dict] = []
        self.events: list[dict] = []
        self.model_requested: str | None = None
        self.model_served: str | None = None
        self.path: str | None = None

    def add(self, event) -> None:
        """Fold one ADK event in. Never raises — see the guard below."""
        meta = (getattr(event, "custom_metadata", None) or {}).get("finchat") or {}
        # First non-null wins: a turn is several model calls and they are the same
        # version, so the first one to report is as good as any and later Nones (a
        # fallback event, say) must not erase it.
        self.model_requested = self.model_requested or meta.get("model_requested")
        self.model_served = self.model_served or meta.get("model_served")
        self.path = self.path or meta.get("path")

        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            return
        if event.is_final_response():
            self.text = "".join(getattr(p, "text", None) or "" for p in parts)
        if not self.collect_trajectory:
            return
        # Guarded per event. An argument or tool result that will not serialize is a gap
        # on the evaluation side; letting it raise would make it a 500 on the turn itself,
        # which is the trade test_tools.py exists to hold the line on.
        try:
            for p in parts:
                fc = getattr(p, "function_call", None)
                if fc is not None:
                    self.calls.append({"name": fc.name,
                                       "arguments": dict(fc.args or {})})
                fr = getattr(p, "function_response", None)
                if fr is not None:
                    self.outputs.append({"name": fr.name, "response": fr.response})
            self.events.append({
                "author": getattr(event, "author", None),
                "content": content.model_dump(mode="json", exclude_none=True,
                                              by_alias=True),
            })
        except Exception as e:  # pragma: no cover - serialization edge
            self.events.append({"author": "finchat",
                                "harvest_error": type(e).__name__})

    def payload(self, session_id: str) -> dict:
        """The /chat response body.

        `model_served` of None means the surface did not report it — never backfilled
        from the requested id, which would record an assumption as evidence
        (scripts/model_pins.py).
        """
        body = {"response": self.text, "session_id": session_id,
                "model_requested": self.model_requested,
                "model_served": self.model_served}
        if self.collect_trajectory:
            body["trajectory"] = self.calls
            body["tool_outputs"] = self.outputs
            # ADK-shaped, because that is the `intermediate_events` column the Gen AI
            # evaluation service reads for its agent metrics (eval/pipelines/vertex_eval.py).
            body["intermediate_events"] = self.events
            body["gateway_path"] = self.path
        return body
