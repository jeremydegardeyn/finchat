"""
Distributed tracing — OpenTelemetry spans to Cloud Trace (docs/30, ADR-0035).

One module, every service. A turn on this platform crosses the BFF, the AI gateway, an
ADK agent, one or two system APIs and BigQuery; until this existed, `conversation_log`
recorded the whole crossing as a single `latency_ms` and there was no way to ask which
hop spent it.

Three design rules, all load-bearing:

1. **A span carries structure and identifiers, never content.** This is the same rule
   `control_events.py` enforces for the ServiceNow envelope, for a stronger reason: ADK
   instruments itself and sets `gcp.vertex.agent.llm_request` / `llm_response` to the
   serialized prompt and answer. Export those and Cloud Trace becomes a third copy of
   customer text, outside the Model Armor sanitize log that is the one sanctioned place
   for it and readable by everyone holding `roles/cloudtrace.user` — a much wider group
   than the Model Armor readers. So content is removed on the way out, by two independent
   mechanisms: a deny-list of the keys known to carry it, and a length ceiling that
   catches the ones nobody has thought of yet. `test_tracing.py` pins both. Redaction you
   have to remember to apply is redaction you eventually forget.

2. **Our own attributes are an allow-list, not a convention.** `span()` accepts only keys
   in `ATTR_KEYS`. A new key is a deliberate edit here, reviewed against rule 1, rather
   than whatever a call site felt like passing — which is how a `question=` kwarg ends up
   in a span six months from now.

3. **Tracing can never fail a request.** Every public function swallows its own errors,
   `init()` is idempotent, and with `TRACING` unset the whole module degrades to
   no-op context managers that still yield. A service deployed with this module and the
   flag off behaves exactly as it did before.

Why not ADK's own `get_gcp_exporters(enable_cloud_tracing=True)`, which is what Vertex AI
Agent Engine wires up for you: it exports OTLP to `telemetry.googleapis.com`, which adds
`opentelemetry-exporter-otlp-proto-http` plus a Resource Manager lookup, and it hands back
a finished span processor with no seam to redact in. Rule 1 is the requirement that decides
it — the Cloud Trace exporter is one dependency and, more importantly, an exporter object
this module can wrap. If the content control ever moves upstream, ADK's helper becomes a
one-line switch. See ADR-0035.

Propagation is deliberately dual. `inject()` writes W3C `traceparent` (both ends of every
hop here are ours, and it is what the SDK reads natively) AND `X-Cloud-Trace-Context`
(what Cloud Run stamps on its own request logs, and what `control_events` already carries
into ServiceNow). Writing both is what makes the trace id in a ServiceNow event resolve to
a span tree and to the log entries of every service on the path, rather than to the BFF's
log entries alone.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

SERVICE = ""
_READY = False
_INIT_TRIED = False

# Gate, matching the CONTROL_EVENTS idiom: deploying this module changes nothing until
# the flag is set. Off, every function here is a no-op that still yields.
ENABLED = os.getenv("TRACING", "").lower() in ("1", "true", "yes")
# Head sampling. FinChat's volume is a few thousand turns a day, well inside Cloud
# Trace's free tier, so the default keeps every trace — a sampled-away trace is exactly
# the one someone asks about. The knob exists for load tests.
SAMPLE_RATIO = float(os.getenv("TRACING_SAMPLE", "1.0"))

# --- the content control ----------------------------------------------------------

# Any string attribute longer than this is dropped, whatever its key. This is the half
# of the control that works on attributes nobody has enumerated: a prompt, an answer, a
# SQL string or a row payload cannot be squeezed under it, and no legitimate identifier
# here needs more. Model ids and reason codes are the longest real values (~50 chars).
MAX_ATTR_LEN = 128

# The half that works by name, for the keys already known to carry content. ADK sets all
# five of these; `gcp.vertex.agent.data` is the generic one and the reason this is a
# prefix match rather than a set of exact keys.
CONTENT_KEYS = frozenset({
    "gcp.vertex.agent.llm_request",
    "gcp.vertex.agent.llm_response",
    "gcp.vertex.agent.tool_call_args",
    "gcp.vertex.agent.tool_response",
    "gcp.vertex.agent.data",
    # OTel's gen_ai semantic conventions for the same thing, in case an instrumentation
    # library on the path emits them instead.
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
})
CONTENT_PREFIXES = ("gen_ai.prompt.", "gen_ai.completion.")

# Our own vocabulary. A key outside this set is a bug in the caller, not user input —
# `span()` drops it rather than exporting something unreviewed.
ATTR_KEYS = frozenset({
    # Who and which conversation. Hashes and opaque ids only: `principal_hash` is the
    # same salted hash control_events emits, never the email.
    "finchat.conversation_id",
    "finchat.session_key",
    "finchat.principal_hash",
    "finchat.persona",
    "finchat.turn_index",
    # Which surface, and how the request was routed.
    "finchat.channel",
    "finchat.route",
    "finchat.env",
    "finchat.upstream",
    "finchat.http_status",
    # Model provenance (ADR-0022): requested vs served are different facts.
    "finchat.model_requested",
    "finchat.model_served",
    # Gateway transit (ADR-0024): the per-request version of the in-process counters.
    "finchat.gateway_outcome",
    "finchat.bypass_reason",
    # Controls. Filter NAMES are detector labels, not content — the same reasoning that
    # lets control_events carry `filters`.
    "finchat.armor_action",
    "finchat.armor_filters",
    "finchat.safety_action",
    "finchat.safety_tier",
    # Data access shape, for the hop that is usually the slow one.
    "finchat.rows",
    "finchat.tool",
    "finchat.dataset",
    "finchat.cache",
    # Failure. The TYPE of an exception, never its message: a BigQuery error message
    # quotes the SQL, and the SQL quotes the question.
    "finchat.error_type",
})


def _clean(key: str, value):
    """One attribute, or None if it must not leave the process.

    Applied to our own spans (against ATTR_KEYS) and to foreign ones (against
    CONTENT_KEYS and the length ceiling) by the two callers below.
    """
    if key in CONTENT_KEYS or key.startswith(CONTENT_PREFIXES):
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        # A sequence of short labels (armor filters, finish reasons) is fine; a sequence
        # of anything long is not, and the ceiling decides per element.
        out = [v for v in (_clean(key, v) for v in value) if v is not None]
        return out or None
    s = str(value)
    if len(s) > MAX_ATTR_LEN:
        return None
    return s


def _own_attrs(kw: dict) -> dict:
    """Our own attributes: allow-listed key, then the same content checks."""
    out = {}
    for k, v in (kw or {}).items():
        if v is None:
            continue
        key = k if k.startswith("finchat.") else f"finchat.{k}"
        if key not in ATTR_KEYS:
            continue
        c = _clean(key, v)
        if c is not None:
            out[key] = c
    return out


def redact(attributes) -> dict:
    """Foreign attributes (ADK's, chiefly) stripped of content but otherwise kept.

    A deny-list rather than an allow-list on purpose: ADK's structural attributes —
    `gcp.vertex.agent.invocation_id`, `session_id`, `gen_ai.usage.input_tokens`,
    `gen_ai.request.model` — are the reason to export its spans at all, and an
    allow-list here would mean re-deriving their names on every ADK upgrade.
    """
    out = {}
    for k, v in (attributes or {}).items():
        c = _clean(k, v)
        if c is not None:
            out[k] = c
    return out


class _RedactedSpan:
    """A finished span with its content attributes removed.

    A delegating proxy rather than a mutation of the span: `ReadableSpan.attributes` is a
    read-only mapping, and reaching into the private field that backs it would couple this
    to an SDK internal. The Cloud Trace exporter reads spans by attribute access only (no
    isinstance on the span itself), so delegation is sufficient and stays sufficient.
    """

    __slots__ = ("_s", "_attrs", "_events")

    def __init__(self, s):
        self._s = s
        self._attrs = redact(s.attributes)
        self._events = None

    @property
    def attributes(self):
        return self._attrs

    @property
    def events(self):
        # Events are a second carrier: an instrumentation library that records a prompt
        # as an event attribute would walk straight past a check that only reads
        # `attributes`. Names are kept, attributes go through the same filter.
        if self._events is None:
            evs = []
            for e in (self._s.events or ()):
                try:
                    evs.append(type(e)(name=e.name, attributes=redact(e.attributes),
                                       timestamp=e.timestamp))
                except Exception:
                    evs.append(e)
            self._events = tuple(evs)
        return self._events

    def __getattr__(self, name):
        return getattr(self._s, name)


class RedactingExporter:
    """Wraps a SpanExporter so nothing leaves without passing `redact()`.

    Placed here, in the export path, rather than at the call sites: the spans that carry
    content are ADK's, and ADK is not a call site we own. A control that only works on
    the spans we remembered to write is not a control.
    """

    def __init__(self, inner):
        self._inner = inner

    def export(self, spans):
        return self._inner.export([_RedactedSpan(s) for s in spans])

    def shutdown(self):
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:
            return False


# --- setup ------------------------------------------------------------------------

def init(service: str, env: str = "") -> bool:
    """Wire the global tracer provider. Idempotent, never raises, safe to call at import.

    Returns True when spans will actually be exported, so a caller can log the posture
    once at startup instead of wondering.
    """
    global SERVICE, _READY, _INIT_TRIED
    SERVICE = service or SERVICE
    if _READY or _INIT_TRIED:
        return _READY
    _INIT_TRIED = True
    if not ENABLED:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import (ParentBased,
                                                      TraceIdRatioBased)
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        resource = Resource.create({
            "service.name": service,
            "service.namespace": "finchat",
            "deployment.environment": env or os.getenv("ENV", "") or _env_from_dataset(),
        })
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(SAMPLE_RATIO)),
        )
        # BatchSpanProcessor, and the reason matters on Cloud Run: it exports from a
        # background thread, and Cloud Run throttles CPU once the response is sent. The
        # batch therefore goes out on a LATER request or at shutdown, which is fine for a
        # trace (it is diagnostic, read minutes later) and is exactly why the same
        # pattern was wrong for conversation_log, which the BFF awaits in-request.
        provider.add_span_processor(
            BatchSpanProcessor(RedactingExporter(CloudTraceSpanExporter())))
        trace.set_tracer_provider(provider)
        _READY = True
    except Exception:
        # No trace is a degraded diagnostic, not a degraded product.
        _READY = False
    return _READY


def _env_from_dataset() -> str:
    """dev/prod from a dataset suffix, the way `_control_ctx` derives it.

    Advisory only, same caveat as docs/26 F18: the authoritative environment is
    `resource.labels.env` on the log entry, which the workload cannot forge.
    """
    for var in ("SILVER_DATASET", "GOLD_DATASET", "LOANS_DATASET", "KB_DATASET"):
        v = os.getenv(var, "")
        if "_" in v:
            return v.rsplit("_", 1)[-1]
    return "unknown"


def _tracer():
    from opentelemetry import trace
    return trace.get_tracer("finchat." + (SERVICE or "service"))


# --- propagation ------------------------------------------------------------------

# "TRACE_ID/SPAN_ID;o=1" — 32 hex, then a DECIMAL span id. The decimal is the detail
# that makes hand-parsing worth a test: every other trace format here is hex.
_GCP_TRACE_RE = re.compile(r"^([0-9a-fA-F]{32})/(\d+)(?:;o=([01]))?")
_W3C_RE = re.compile(r"^00-([0-9a-fA-F]{32})-([0-9a-fA-F]{16})-([0-9a-fA-F]{2})$")


def _parse_parent(headers):
    """A remote SpanContext from inbound headers, or None.

    W3C first (what our own services send), then `X-Cloud-Trace-Context` (what Cloud Run
    stamps when the caller is a browser or another GCP product). Taking the second is what
    puts our spans on the same trace id as Cloud Run's own request log entry, which is the
    id `control_events` hands to ServiceNow.
    """
    try:
        from opentelemetry.trace import (SpanContext, TraceFlags, NonRecordingSpan)
        from opentelemetry import context as otel_context
        from opentelemetry.trace import set_span_in_context

        get = headers.get if hasattr(headers, "get") else (lambda k, d=None: None)
        raw = get("traceparent") or get("Traceparent") or ""
        m = _W3C_RE.match(raw.strip())
        if m:
            tid, sid, flags = int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)
        else:
            raw = get("x-cloud-trace-context") or get("X-Cloud-Trace-Context") or ""
            m = _GCP_TRACE_RE.match(raw.strip())
            if not m:
                return None
            tid, sid = int(m.group(1), 16), int(m.group(2))
            flags = 1 if (m.group(3) or "1") == "1" else 0
        if not tid or not sid:
            return None
        ctx = SpanContext(trace_id=tid, span_id=sid, is_remote=True,
                          trace_flags=TraceFlags(flags))
        del otel_context  # imported for clarity about what set_span_in_context returns
        return set_span_in_context(NonRecordingSpan(ctx))
    except Exception:
        return None


def inject(headers: dict) -> dict:
    """Stamp the current span onto an outbound header dict, in both formats.

    Mutates and returns `headers` so a call site can wrap its existing dict in place.
    Silent no-op when there is no current span, which is what makes it safe to call
    unconditionally from every proxy path.
    """
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if not ctx or not ctx.is_valid:
            return headers
        tid = format(ctx.trace_id, "032x")
        sid = format(ctx.span_id, "016x")
        sampled = 1 if ctx.trace_flags.sampled else 0
        headers["traceparent"] = f"00-{tid}-{sid}-{sampled:02x}"
        # Decimal span id, per the GCP format. A hex value here is accepted by nothing
        # and fails silently, which is the worst possible failure for a correlation id.
        headers["X-Cloud-Trace-Context"] = f"{tid}/{ctx.span_id};o={sampled}"
    except Exception:
        pass
    return headers


def current_trace_id() -> str:
    """The active trace id as 32 hex chars, or "".

    This is the value that belongs in `conversation_log.trace_id` and in the
    `control_events` envelope: it is the join key between a captured turn, a redacted
    ServiceNow event, the span tree and every service's log entries for that request.
    """
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx and ctx.is_valid else ""
    except Exception:
        return ""


# --- spans ------------------------------------------------------------------------

@contextmanager
def server_span(name: str, headers=None, **attrs):
    """The inbound span for a request, continuing the caller's trace when there is one."""
    if not _READY:
        yield None
        return
    try:
        from opentelemetry.trace import SpanKind
        with _tracer().start_as_current_span(
                name, context=_parse_parent(headers or {}), kind=SpanKind.SERVER,
                attributes=_own_attrs(attrs)) as sp:
            yield sp
    except Exception:
        yield None


@contextmanager
def span(name: str, client: bool = False, **attrs):
    """A child span. `client=True` marks an outbound call, which Cloud Trace renders as
    the caller's half of a hop."""
    if not _READY:
        yield None
        return
    try:
        from opentelemetry.trace import SpanKind
        with _tracer().start_as_current_span(
                name, kind=SpanKind.CLIENT if client else SpanKind.INTERNAL,
                attributes=_own_attrs(attrs)) as sp:
            yield sp
    except Exception:
        yield None


def set_attrs(**attrs) -> None:
    """Add allow-listed attributes to the current span. Most useful for the facts only
    known after the call — status, served model, row count, gateway outcome."""
    if not _READY:
        return
    try:
        from opentelemetry import trace
        sp = trace.get_current_span()
        for k, v in _own_attrs(attrs).items():
            sp.set_attribute(k, v)
    except Exception:
        pass


def record_error(exc: BaseException) -> None:
    """Mark the current span failed, carrying the exception TYPE and nothing else.

    Not `span.record_exception()`, which attaches the message and the stack trace: a
    BigQuery error message quotes the failing SQL, and on the analyst path the SQL was
    generated from the user's question. The type name is what a latency investigation
    needs; the message is already in the service's own logs, under their access controls.
    """
    if not _READY:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
        sp = trace.get_current_span()
        sp.set_status(Status(StatusCode.ERROR))
        sp.set_attribute("finchat.error_type", type(exc).__name__[:MAX_ATTR_LEN])
    except Exception:
        pass


def flush(timeout_millis: int = 2000) -> None:
    """Push pending spans now.

    For the short-lived callers — `live_eval.py`, the eval harness, any script that exits
    before BatchSpanProcessor's timer fires. A long-running service never needs it.
    """
    if not _READY:
        return
    try:
        from opentelemetry import trace
        trace.get_tracer_provider().force_flush(timeout_millis)
    except Exception:
        pass
