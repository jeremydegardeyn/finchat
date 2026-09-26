"""
Guard tests for the tracing module (ADR-0035).

The first group is the content control, and it is the reason this file exists: the whole
value of exporting ADK's spans is the structure they carry, and the whole risk is the
prompt and answer text they also carry. These tests are what keeps the second from
shipping with the first. They need no OpenTelemetry — `redact()` and `_own_attrs()` are
plain dict functions on purpose, so the control is testable without the SDK.

The second group needs the SDK and skips without it; CI installs it.
"""
from __future__ import annotations

import pytest

import tracing as t


# =============================== the content control ===============================

# ADK 2.2.0's real attribute keys, read off the wheel rather than remembered. The five
# content ones and the structural ones that make its spans worth exporting.
ADK_SPAN = {
    "gen_ai.system": "gcp.vertex.agent",
    "gen_ai.request.model": "gemini-2.5-flash",
    "gen_ai.usage.input_tokens": 1843,
    "gen_ai.usage.output_tokens": 122,
    "gcp.vertex.agent.invocation_id": "e-9f3c1a20-1111-4b6d-8c2e-77aa",
    "gcp.vertex.agent.session_id": "analyst-kb",
    "gcp.vertex.agent.event_id": "aBcD1234",
    "gcp.vertex.agent.llm_request": '{"contents":[{"role":"user","parts":[{"text":"my card ending 4417 was charged twice"}]}]}',
    "gcp.vertex.agent.llm_response": '{"candidates":[{"content":{"parts":[{"text":"I can see two POSTED debits of $42.10 on account ACC-0007"}]}}]}',
    "gcp.vertex.agent.tool_call_args": '{"account_id":"ACC-0007","limit":5}',
    "gcp.vertex.agent.tool_response": '[{"amount":-42.10,"merchant":"BLUE BOTTLE"}]',
    "gcp.vertex.agent.data": '{"text":"anything at all"}',
}


def test_adk_content_attributes_never_export():
    out = t.redact(ADK_SPAN)
    for k in ("gcp.vertex.agent.llm_request", "gcp.vertex.agent.llm_response",
              "gcp.vertex.agent.tool_call_args", "gcp.vertex.agent.tool_response",
              "gcp.vertex.agent.data"):
        assert k not in out, f"{k} carries prompt/answer text and must not be exported"
    # And no value that survived contains any of the content we planted.
    blob = " ".join(str(v) for v in out.values())
    for leak in ("4417", "charged twice", "BLUE BOTTLE", "42.10", "ACC-0007"):
        assert leak not in blob


def test_adk_structural_attributes_do_export():
    """The deny-list must not become an accidental allow-list of nothing — these are
    why the spans are exported at all."""
    out = t.redact(ADK_SPAN)
    assert out["gen_ai.system"] == "gcp.vertex.agent"
    assert out["gen_ai.request.model"] == "gemini-2.5-flash"
    assert out["gen_ai.usage.input_tokens"] == 1843
    assert out["gcp.vertex.agent.invocation_id"].startswith("e-9f3c1a20")
    assert out["gcp.vertex.agent.session_id"] == "analyst-kb"


def test_length_ceiling_catches_an_unknown_content_key():
    """The second, independent mechanism. A content attribute under a key nobody has
    enumerated — a future ADK release, another instrumentation library — is still
    dropped, because content cannot fit under the ceiling."""
    novel = {"some.future.library.prompt_text": "x" * (t.MAX_ATTR_LEN + 1)}
    assert t.redact(novel) == {}


def test_ceiling_is_on_the_value_not_the_key():
    assert t.redact({"a.short.value": "y" * t.MAX_ATTR_LEN}) == {
        "a.short.value": "y" * t.MAX_ATTR_LEN}
    assert t.redact({"a.short.value": "y" * (t.MAX_ATTR_LEN + 1)}) == {}


def test_sequence_values_are_filtered_elementwise():
    """Armor filter names are a legitimate list of short labels; a list of long strings
    is the same leak with an extra layer of wrapping."""
    assert t.redact({"finchat.armor_filters": ["pi_and_jailbreak", "sdp"]}) == {
        "finchat.armor_filters": ["pi_and_jailbreak", "sdp"]}
    assert t.redact({"x.y": ["z" * (t.MAX_ATTR_LEN + 1)]}) == {}


def test_numbers_and_bools_pass_untouched():
    out = t.redact({"n": 42, "f": 1.5, "b": True})
    assert out == {"n": 42, "f": 1.5, "b": True}


# ============================= our own attribute vocabulary =========================

def test_attr_keys_is_pinned():
    """The allow-list is a reviewed contract, exactly like ENVELOPE_KEYS in
    control_events.py. Adding a key means editing this test, which means someone reads
    rule 1 in the module docstring before a new attribute ships."""
    assert t.ATTR_KEYS == frozenset({
        "finchat.conversation_id", "finchat.session_key", "finchat.principal_hash",
        "finchat.persona", "finchat.turn_index",
        "finchat.channel", "finchat.route", "finchat.env", "finchat.upstream",
        "finchat.http_status",
        "finchat.model_requested", "finchat.model_served",
        "finchat.gateway_outcome", "finchat.bypass_reason",
        "finchat.armor_action", "finchat.armor_filters",
        "finchat.safety_action", "finchat.safety_tier",
        "finchat.rows", "finchat.tool", "finchat.dataset", "finchat.cache",
        "finchat.error_type",
    })


def test_no_attr_key_is_content_shaped():
    """A key whose name invites content is the failure this whole file guards against."""
    for k in t.ATTR_KEYS:
        assert not any(w in k for w in ("question", "answer", "prompt", "text", "sql",
                                        "message", "content", "email", "response"))


def test_bare_keys_are_namespaced_and_unknown_keys_dropped():
    assert t._own_attrs({"persona": "analyst"}) == {"finchat.persona": "analyst"}
    assert t._own_attrs({"question": "how much did I spend"}) == {}
    assert t._own_attrs({"finchat.question": "how much did I spend"}) == {}


def test_own_attrs_drops_none_but_keeps_zero():
    """A zero row count and a zero safety tier are facts; None is an absent one."""
    out = t._own_attrs({"rows": 0, "safety_tier": 0, "model_served": None})
    assert out == {"finchat.rows": 0, "finchat.safety_tier": 0}


def test_an_allow_listed_key_still_obeys_the_ceiling():
    """Allow-listing a key is not permission to put a paragraph in it."""
    assert t._own_attrs({"route": "r" * (t.MAX_ATTR_LEN + 1)}) == {}


# ================================== propagation ====================================

def test_parses_cloud_run_header_with_decimal_span_id():
    """`X-Cloud-Trace-Context` uses a DECIMAL span id. Reading it as hex yields a
    plausible-looking wrong number, which is the worst kind of correlation bug."""
    pytest.importorskip("opentelemetry")
    from opentelemetry import trace as otrace
    tid = "105445aa7843bc8bf206b12000100000"
    ctx = t._parse_parent({"X-Cloud-Trace-Context": f"{tid}/1234567890;o=1"})
    sc = otrace.get_current_span(ctx).get_span_context()
    assert format(sc.trace_id, "032x") == tid
    assert sc.span_id == 1234567890          # decimal, not 0x1234567890
    assert sc.trace_flags.sampled is True


def test_w3c_header_wins_over_the_gcp_one():
    pytest.importorskip("opentelemetry")
    from opentelemetry import trace as otrace
    w3c_tid = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    gcp_tid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ctx = t._parse_parent({
        "traceparent": f"00-{w3c_tid}-00000000000000ff-01",
        "X-Cloud-Trace-Context": f"{gcp_tid}/99;o=1"})
    sc = otrace.get_current_span(ctx).get_span_context()
    assert format(sc.trace_id, "032x") == w3c_tid


@pytest.mark.parametrize("headers", [
    {},
    {"X-Cloud-Trace-Context": ""},
    {"X-Cloud-Trace-Context": "not-a-trace"},
    {"X-Cloud-Trace-Context": "00000000000000000000000000000000/1;o=1"},   # all-zero id
    {"X-Cloud-Trace-Context": "105445aa7843bc8bf206b12000100000/0;o=1"},   # zero span
    {"traceparent": "00-tooshort-00000000000000ff-01"},
])
def test_malformed_headers_yield_no_parent(headers):
    """A bad header must start a fresh trace, not raise into the request path."""
    pytest.importorskip("opentelemetry")
    assert t._parse_parent(headers) is None


def test_inject_round_trips_and_writes_both_formats():
    pytest.importorskip("opentelemetry")
    from opentelemetry import trace as otrace
    _enable()
    with t.server_span("test.root", {}):
        h = t.inject({})
        assert "traceparent" in h and "X-Cloud-Trace-Context" in h
        tid = t.current_trace_id()
        # The GCP header's span id is decimal; the W3C one's is hex. Same span.
        gcp_tid, rest = h["X-Cloud-Trace-Context"].split("/")
        assert gcp_tid == tid
        dec_span = int(rest.split(";")[0])
        assert format(dec_span, "016x") == h["traceparent"].split("-")[2]
        # And a downstream service parsing what we sent lands on the same trace.
        ctx = t._parse_parent(h)
        sc = otrace.get_current_span(ctx).get_span_context()
        assert format(sc.trace_id, "032x") == tid
        assert sc.span_id == dec_span


def test_inject_is_a_no_op_with_no_current_span():
    """Called unconditionally from every proxy path, so this is the common case when
    tracing is off."""
    assert t.inject({"content-type": "application/json"}) == {
        "content-type": "application/json"}


# ============================ behaviour when disabled ==============================

def test_disabled_module_yields_and_never_raises(monkeypatch):
    """A service deployed with this module and TRACING unset behaves as it did before."""
    monkeypatch.setattr(t, "_READY", False)
    with t.server_span("x", {"traceparent": "garbage"}) as sp:
        assert sp is None
    with t.span("y", client=True, rows=3) as sp:
        assert sp is None
    t.set_attrs(persona="analyst")
    t.record_error(ValueError("boom"))
    t.flush()
    assert t.current_trace_id() == ""


# ========================= the export path, end to end =============================

def _enable():
    """Real SDK, in-memory exporter, redaction wrapper in the path — the arrangement the
    services run, minus Cloud Trace."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(t.RedactingExporter(exporter)))
    # A provider can only be set once per process, so reuse whatever is already global.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(provider)
        holder = provider
    else:
        holder = trace.get_tracer_provider()
        holder.add_span_processor(SimpleSpanProcessor(t.RedactingExporter(exporter)))
    t._READY = True
    t.SERVICE = "test"
    return exporter, holder


def test_a_real_span_carrying_content_is_redacted_on_export():
    """The integration the unit tests above imply: content set on a live span, through a
    real SpanProcessor, does not reach the exporter."""
    pytest.importorskip("opentelemetry")
    exporter, _ = _enable()
    exporter.clear()
    with t.span("call_llm") as sp:
        # Bypassing _own_attrs the way ADK does — it sets attributes on the span itself,
        # which is exactly why the control lives in the exporter and not in `span()`.
        sp.set_attribute("gcp.vertex.agent.llm_request", "the customer's actual question")
        sp.set_attribute("gen_ai.usage.input_tokens", 99)
    got = exporter.get_finished_spans()[-1]
    assert "gcp.vertex.agent.llm_request" not in got.attributes
    assert got.attributes["gen_ai.usage.input_tokens"] == 99


def test_span_events_are_redacted_too():
    pytest.importorskip("opentelemetry")
    exporter, _ = _enable()
    exporter.clear()
    with t.span("call_llm") as sp:
        sp.add_event("gen_ai.content.prompt",
                     {"gen_ai.prompt": "my card ending 4417", "index": 0})
    got = exporter.get_finished_spans()[-1]
    ev = got.events[-1]
    assert ev.name == "gen_ai.content.prompt"
    assert "gen_ai.prompt" not in ev.attributes
    assert ev.attributes["index"] == 0


def test_record_error_carries_the_type_and_not_the_message():
    """A BigQuery error message quotes the SQL, and on the analyst path the SQL was
    generated from the question."""
    pytest.importorskip("opentelemetry")
    exporter, _ = _enable()
    exporter.clear()
    with t.span("bq.query"):
        t.record_error(ValueError("Syntax error near 'my card ending 4417'"))
    got = exporter.get_finished_spans()[-1]
    assert got.attributes["finchat.error_type"] == "ValueError"
    blob = " ".join(str(v) for v in got.attributes.values()) + " ".join(
        str(e.attributes) for e in (got.events or ()))
    assert "4417" not in blob


def test_set_attrs_reaches_the_exported_span():
    pytest.importorskip("opentelemetry")
    exporter, _ = _enable()
    exporter.clear()
    with t.span("gateway.complete", client=True, route="analyst_semantics"):
        t.set_attrs(gateway_outcome="transited", model_served="gemini-2.5-flash-002")
    got = exporter.get_finished_spans()[-1]
    assert got.attributes["finchat.route"] == "analyst_semantics"
    assert got.attributes["finchat.gateway_outcome"] == "transited"
    assert got.attributes["finchat.model_served"] == "gemini-2.5-flash-002"


# ==================== two services, one trace (the whole point) =====================

def test_a_hop_between_two_services_produces_one_trace():
    """The claim this increment rests on, end to end.

    Service A takes an inbound browser request carrying only Cloud Run's
    `X-Cloud-Trace-Context`, calls service B with the headers `inject()` produced, and B
    continues the same trace. Before ADR-0035 the header was not forwarded at all, so B
    started a trace of its own and the two halves of a turn were unrelated — which is the
    regression this test exists to catch, because it is invisible until someone needs it.
    """
    pytest.importorskip("opentelemetry")
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    exporter, _ = _enable()
    exporter.clear()

    # Cloud Run stamps this on the browser's request. Decimal span id, as always.
    inbound = {"X-Cloud-Trace-Context": "4bf92f3577b34da6a3ce929d0e0e4736/9999;o=1"}
    forwarded: dict = {}

    app_a = fastapi.FastAPI()

    @app_a.middleware("http")
    async def mw(request, call_next):
        with t.server_span("GET /api/agent/{path}", request.headers,
                           route="/api/agent/{path}"):
            # What _proxy does: a fresh header dict, then inject.
            forwarded.update(t.inject({"content-type": "application/json"}))
            return await call_next(request)

    @app_a.get("/api/agent/chat")
    def handler():
        return {"ok": True}

    assert TestClient(app_a).get("/api/agent/chat", headers=inbound).status_code == 200

    # Service B, receiving what A sent.
    ctx = t._parse_parent(forwarded)
    from opentelemetry import trace as otrace
    b_parent = otrace.get_current_span(ctx).get_span_context()

    a_span = next(s for s in exporter.get_finished_spans()
                  if s.name == "GET /api/agent/{path}")
    one_trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    # All three agree: the browser's trace, A's span, and what B will attach to.
    assert format(a_span.context.trace_id, "032x") == one_trace
    assert format(b_parent.trace_id, "032x") == one_trace
    # And B's parent is A's span, not the browser's — a tree, not two roots.
    assert b_parent.span_id == a_span.context.span_id
    assert a_span.parent.span_id == 9999
