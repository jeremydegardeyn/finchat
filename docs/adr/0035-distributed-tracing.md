# ADR-0035 — Distributed tracing on Cloud Trace: spans carry structure, never content

- **Status:** Accepted
- **Date:** 2026-09-25
- **Deciders:** Principal Data Architect
- **Context tags:** observability, AgentOps, cost engineering, responsible AI, controls
- **Related:** ADR-0004 / ADR-0010 (Agent Engine vs Cloud Run), ADR-0008 (Model Armor),
  ADR-0015 (live evaluation), ADR-0022 (model pinning), ADR-0024 (AI gateway),
  ADR-0026 / docs/26 (controls alerting), ADR-0030 (API-led layering),
  ADR-0034 (conversation safety), docs/30

## Context

One customer turn on this platform crosses, in order: the SPA, the BFF, the session lease,
the safety classifier (a model call), Model Armor twice, the agent on Cloud Run, the ADK
runner, one to three tool calls into the transactions API, and BigQuery or Bigtable. An
analyst turn crosses the intent router (a model call), then one of four routes, one of
which is Conversational Analytics with a 150-second timeout.

Before this ADR the entire crossing was recorded as **one integer**:
`conversation_log.latency_ms`. That number is enough to compute a p95 and put it on the
Admin card, which ADR-0015 did, and it is not enough to act on: a turn that took 9,400 ms
could have spent it on a Cloud Run cold start, on the gateway, on the classifier, on
`VECTOR_SEARCH`, or on Conversational Analytics re-sending the whole semantic perimeter
because the persistent Data Agent was missing in that environment. Every one of those has a
different fix and nothing distinguished them.

Three narrower gaps followed from the same absence.

**First, the trace id already in the control-event envelope pointed at nothing.**
`control_events.py` carries `trace` so a ServiceNow responder can pivot from the redacted
event to Model Armor's sanitize log (docs/26). The BFF read it from
`X-Cloud-Trace-Context`, which Cloud Run stamps — but `_proxy` built its outbound header
dict fresh, which is correct for `Authorization` and was silently wrong for correlation.
Nothing propagated the header, so every downstream service began its own trace, and the id
in a ServiceNow event resolved to the BFF's log entries and no others.

**Second, the gateway transit share was a process-level counter.** ADR-0024 exports
`transited / bypass_unconfigured / bypass_error / blocked` at `/api/gateway/transit`. A
share of 0.94 is a true and unusable number: it does not say which calls bypassed, under
which route, or whether the bypasses were the slow ones.

**Third, and this is the one that motivated the increment**, ADR-0010 recorded "native
tracing" as a capability given up by moving the agents off Vertex AI Agent Engine onto Cloud
Run. That turns out to be wrong in an interesting way. ADK instruments *itself*: it creates
spans named `invoke_agent`, `call_llm` and `execute_tool` under the tracer
`gcp.vertex.agent`, carrying the invocation id, the session id, the requested model and the
input/output token counts. It has been building them all along on Cloud Run and dropping
every one into the no-op default `TracerProvider`, because nothing here ever configured a
real one. Agent Engine's tracing *is* that configuration. What ADR-0010 gave up was not the
capability; it was ten lines of exporter wiring.

## Decision

Export OpenTelemetry spans to **Cloud Trace** from every service, from one canonical module
(`observability/tracing.py`), under a rule that is enforced in the export path rather than
asked of call sites: **a span carries structure and identifiers, never content.**

### Why the content rule is the load-bearing part

ADK's `call_llm` span sets `gcp.vertex.agent.llm_request` and
`gcp.vertex.agent.llm_response` to the serialized prompt and answer, and its `execute_tool`
span sets `tool_call_args` and `tool_response`. Turning on the exporter naively therefore
does something this platform has spent four ADRs preventing: it creates a **third copy of
customer content**, in a store that is neither Model Armor's sanitize log (the one
sanctioned place for a flagged prompt, reachable only under GCP IAM) nor a governed
BigQuery dataset, readable by everyone holding `roles/cloudtrace.user` — a far wider group
than the Model Armor readers — and outside the `control_events.py` guarantee that the
alerting path has no free-text field at all.

So content is removed on the way out, by **two independent mechanisms**:

1. A deny-list of the keys known to carry it — ADK's five, plus the OTel `gen_ai`
   content conventions in case another instrumentation library joins the path.
2. A **length ceiling** (128 chars) applied to every string attribute whatever its key.
   This is the half that works on attributes nobody has enumerated: a prompt, an answer, a
   generated SQL string or a row payload cannot fit under it, and no legitimate identifier
   here needs more.

Both are applied by `RedactingExporter`, which wraps the Cloud Trace exporter, and both are
pinned by `observability/test_tracing.py` against ADK 2.2.0's real attribute keys (read off
the wheel, not remembered). Span **events** go through the same filter, because an event
attribute is a second carrier that a check reading only `attributes` walks straight past.

The rule is enforced in the exporter and not at the call sites for a specific reason: the
spans that carry content are ADK's, and ADK is not a call site we own. A control that only
works on the spans we remembered to write is not a control.

FinChat's own attributes are additionally an **allow-list** (`ATTR_KEYS`), the same shape as
`ENVELOPE_KEYS` in `control_events.py`. A new attribute is a reviewed edit to that set, not
whatever a call site felt like passing.

### Why not ADK's own exporter

ADK 2.2.0 ships `telemetry.google_cloud.get_gcp_exporters(enable_cloud_tracing=True)`, which
is what Agent Engine wires up. It is the vendor path and it is rejected here on two counts:
it exports OTLP to `telemetry.googleapis.com`, which pulls in
`opentelemetry-exporter-otlp-proto-http` and a Resource Manager lookup; and it returns a
finished `BatchSpanProcessor` with no seam to redact in. `opentelemetry-exporter-gcp-trace`
writes the Cloud Trace API directly, is one dependency, and hands back an exporter object
this module can wrap. If the content control ever moves upstream, ADK's helper becomes a
one-line switch.

### Agent Engine, priced

The obvious alternative reading of the third gap is "re-adopt Agent Engine and get the
tracing that comes with it". Costed against ADR-0010's own numbers, that is
**$150–440/month** — a per-engine compute baseline of roughly $75–110 with no clean
scale-to-zero, times the transactions agent and the loan agent set, times dev and prod — to
obtain spans this image already builds. The Cloud Run path costs one IAM role and stays
inside Cloud Trace's 2.5M-span free tier at FinChat's volume of a few thousand turns a day.
ADR-0010 stands, and its "Consequences" line about tracing is now closed rather than open.

### What is instrumented

| Service | Spans |
|---|---|
| UI BFF | root span per request (route template, never the resolved path), the 3-way analyst router's choice, gateway transit outcome, both Model Armor screens, the safety classifier and trajectory write, the CA hop (Data Agent vs inline), `VECTOR_SEARCH`, every proxied hop |
| Agent (Cloud Run) | ADK's own `invoke_agent` / `call_llm` / `execute_tool`, content stripped, plus the served model and the gateway routing decision |
| Transactions API | the ADR-0017 hot/cold decision — which of Bigtable and BigQuery actually served the read |
| Loan API, Process API | server span; the process layer propagates into both system APIs it composes (ADR-0030) |
| MCP server | HTTP transport only, and propagation into the backends — over stdio there is nothing to correlate to and no credentials to export with |

### Propagation is dual, deliberately

`inject()` writes **both** W3C `traceparent` and `X-Cloud-Trace-Context` on every outbound
hop. The first is what the SDK reads natively and both ends of every hop here are ours; the
second is what Cloud Run stamps on its own request logs and what `control_events` already
carries into ServiceNow. Writing both is what makes a trace id in a ServiceNow event resolve
to a span tree *and* to the log entries of every service on the path. Inbound, the parent is
taken from `traceparent` first and `X-Cloud-Trace-Context` second, so a browser request that
only Cloud Run has touched still seeds the tree with the id its request log will carry.

One detail is worth a test of its own: `X-Cloud-Trace-Context` uses a **decimal** span id
where every other trace format here is hex. Read as hex it yields a plausible-looking wrong
number, which is the worst possible failure for a correlation id.

### The join key

`conversation_log` gains `trace_id`, read from the active span at capture time. That closes
the loop ADR-0015 opened: a judge score of 0.41 on a turn that took 9.4 seconds becomes a
row you can open. `slow_turns` (the slowest decile of the last 7 days, with the trace id and
the route) is the operational counterpart to `eval_summary`'s quality view.

### Off by default

Gated on `TRACING=1`, the same way `CONTROL_EVENTS` and `SAFETY_SIGNALS` are. Unset, every
function in the module is a no-op that still yields and every service behaves exactly as it
did before. Deploying this increment changes nothing until the variable is set.

## Consequences

- **Spans are diagnostic, so they are exported from a background thread.** Cloud Run
  throttles CPU once the response is sent, so a `BatchSpanProcessor` batch goes out on a
  later request or at shutdown. That is acceptable for a trace read minutes later and is
  exactly why the same pattern was wrong for `conversation_log`, which the BFF awaits
  in-request (ADR-0015).
- **`roles/cloudtrace.agent` is the first project role held by the process, mobile and MCP
  service accounts**, all three of which were empty by design. It is write-only — it grants
  `PatchTraces` and no read of data or of other traces — so the argument that kept them
  empty is not weakened. Reading traces stays a human's grant (`roles/cloudtrace.user`),
  held by no workload here.
- **One canonical module, staged into five build contexts by the build.** Every service but
  the MCP server builds from its own directory and cannot COPY a file above it. The
  alternative is a committed copy per service, which is six files free to disagree about a
  control whose whole purpose is to be identical everywhere.
  `scripts/test_tracing_build.py` holds the staging list, the Dockerfile `COPY` lines and
  the OTel pins against each other.
- **`opentelemetry-sdk` is pinned at 1.41.1 platform-wide because google-adk 2.2.0 caps it
  there.** A service that pins above the cap resolves fine on its own and makes the *agent*
  image the one that fails, which is the most confusing possible place for the error to
  surface. The guard test states the cap so an upgrade has to confront it.
- **`eval_schema.sql` is applied by hand per the runbook, not by CI**, so an image can be
  live before `trace_id` exists. The write path already degraded via
  `ignore_unknown_values`; the Admin log read now degrades the same way, falling back to a
  NULL column rather than 502-ing the whole view.
- **The steward harness (ADR-0021) is not instrumented.** It runs on DBOS with its own
  durable step log, which already records what each step did and when, and it shares the
  agent service account so the role is in place if that changes.
- **A trace is not an audit record.** Spans are sampled-by-policy, dropped on export
  failure, and expire on Cloud Trace's retention. Nothing in the controls chain, the loan
  audit trail or `turn_signals` depends on one, and nothing should: the trace tells you
  where the time went, and the governed stores remain the record of what happened.
