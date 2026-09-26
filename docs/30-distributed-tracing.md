# 30 — Distributed tracing

Operator and developer guide to the span layer ([ADR-0035](adr/0035-distributed-tracing.md)).
It covers what is instrumented, how to turn it on, what a trace is allowed to contain, and
the four questions it exists to answer.

## 1. Why

`conversation_log.latency_ms` records one number per turn. A customer turn crosses the BFF,
a session lease, the safety classifier, Model Armor twice, the agent, the ADK runner, one to
three tool calls and BigQuery or Bigtable; an analyst turn crosses an intent router and then
one of four routes. A single integer over that path supports a p95 on the Admin card and
supports no action, because a 9,400 ms turn spent on a cold start, on the gateway, on
`VECTOR_SEARCH` or on Conversational Analytics has four different fixes and the integer
distinguishes none of them.

## 2. The rule

**A span carries structure and identifiers. It never carries content.**

This is the same rule `ui/control_events.py` enforces for the ServiceNow envelope, and here
it is not optional housekeeping: ADK sets `gcp.vertex.agent.llm_request` and
`llm_response` to the serialized prompt and answer, and `tool_call_args` / `tool_response`
on its tool spans. Exported as-is, Cloud Trace would become a third copy of customer text —
outside Model Armor's sanitize log, which is the one sanctioned place for a flagged prompt,
and readable by everyone holding `roles/cloudtrace.user`.

Two independent mechanisms remove it, both inside `RedactingExporter`, which wraps the
Cloud Trace exporter so nothing can leave without passing through:

| Mechanism | Catches |
|---|---|
| Deny-list (`CONTENT_KEYS`, `CONTENT_PREFIXES`) | the keys known to carry content: ADK's five, plus the OTel `gen_ai` content conventions |
| Length ceiling (`MAX_ATTR_LEN`, 128 chars) | everything nobody has enumerated — a prompt, an answer, a generated SQL string or a row payload cannot fit under it |

Span **events** pass the same filter; an event attribute is a second carrier that a check
reading only `attributes` would walk straight past.

FinChat's own attributes are additionally allow-listed (`ATTR_KEYS`). Adding one is an edit
to that set plus an edit to the test that pins it, which is how someone ends up reading this
section before a new attribute ships.

Two consequences of the rule that surprise people:

- **Span names are route TEMPLATES**, never resolved paths. `GET /v1/accounts/{account_id}/balance`,
  not the account id. A resolved path is unbounded cardinality in Cloud Trace and, on most
  routes here, an identifier.
- **`record_error()` carries the exception TYPE, never its message**, and deliberately does
  not use `span.record_exception()`. A BigQuery error message quotes the failing SQL, and on
  the analyst path the SQL was generated from the customer's question.

## 3. Turning it on

Set the repository variable `TRACING=1` and redeploy. Unset — the default — every function
in `observability/tracing.py` is a no-op that still yields, and every service behaves
exactly as it did before; the module ships inert on purpose, so the increment can be
deployed and this flag flipped separately.

```bash
gh variable set TRACING --body 1
```

The DDL is applied by hand, per the runbook pattern:

```bash
sed -e "s/\${PROJECT}/strongsville-city-schools/g" -e "s/\${ENV}/dev/g" \
    scripts/eval_schema.sql | bq query --use_legacy_sql=false
```

`TRACING_SAMPLE` (default `1.0`) is head sampling. Leave it at 1.0: FinChat's volume is a
few thousand turns a day, well inside Cloud Trace's 2.5M-span monthly free tier, and a
sampled-away trace is reliably the one someone asks about.

To confirm it is live, the BFF returns the trace id on every response:

```bash
curl -si https://finchat.datadinosaur.com/api/config | grep -i x-finchat-trace
```

## 4. What is instrumented

| Service | Spans | The question it answers |
|---|---|---|
| **UI BFF** | root span per request; `analyst.classify_intent`; `analyst.run_<route>`; `gateway.complete`; `armor.screen_prompt` / `armor.screen_response`; `safety.state` / `safety.classify` / `safety.record`; `ca.chat`; `bq.vector_search`; `proxy <upstream>` | where an analyst turn's time goes, and whether the router or the tool spent it |
| **Agent** (Cloud Run) | ADK's own `invoke_agent` / `call_llm` / `execute_tool`, content stripped; plus served model and gateway routing decision | which tool calls the agent chose and what each cost |
| **Transactions API** | `bigtable.point_read` / `bigtable.prefix_scan` vs `bq.get_balance` / `bq.get_transactions` | whether the ADR-0017 hot path actually served the read, in a real environment |
| **Loan API** | server span | |
| **Process API** | server span, and propagation into both system APIs it composes | the LAYER-2 composition cost that ADR-0030 makes structural |
| **MCP server** | HTTP transport only, plus propagation into the backends | what an external agent asked this platform to do |

Not instrumented: the **steward harness** (ADR-0021 — DBOS keeps its own durable step log,
which already records what each step did and when), the **Dataflow pipeline** (Beam has its
own worker metrics), and the **stdio MCP transport** (no credentials to export with, nothing
to correlate to).

## 5. The four questions

### "This turn was slow — which hop?"

```sql
SELECT ts, channel, latency_ms, trace_id, model_served
FROM `strongsville-city-schools.finchat_eval_prod.slow_turns`
LIMIT 20
```

Then open `console.cloud.google.com/traces/list?tid=<trace_id>`. `slow_turns` is the
slowest decile of the last 7 days — the operational counterpart to what `eval_summary` does
for quality.

### "The judge scored this turn 0.41 — what happened?"

`conversation_log.trace_id` joins a captured turn to its span tree. This is the loop
ADR-0015 left open: quality and latency were both recorded and neither was attributable.

### "Which calls bypassed the gateway, and were they the slow ones?"

`/api/gateway/transit` gives a share; `finchat.gateway_outcome` on the `gateway.complete`
span gives it per request, alongside `finchat.route` and the span's duration. A transit
share of 0.94 that turns out to be 6% of calls taking 40 seconds is a different problem from
6% taking 400 ms.

### "A ServiceNow event arrived — what was the request?"

The envelope's `trace` field is now the real trace id (the BFF reads it from the active span
and propagates it to every hop). It resolves to the span tree **and** to every service's log
entries for that request, where before it matched the BFF's entries only. The flagged text
itself stays where docs/26 put it: Model Armor's sanitize log, under IAM, never in the span
and never in the ticket.

## 6. Gotchas

**Spans arrive late.** `BatchSpanProcessor` exports from a background thread, and Cloud Run
throttles CPU once the response is sent, so a batch goes out on a later request or at
shutdown. A trace read minutes later is complete; a trace checked half a second after a
single request against a cold, idle service may not be. Send a second request. (This is
exactly why the same pattern was wrong for `conversation_log`, which the BFF awaits
in-request.)

**`X-Cloud-Trace-Context` span ids are decimal.** Every other trace format here is hex. Read
as hex the value is a plausible-looking wrong number — the worst possible failure for a
correlation id — which is why `test_tracing.py` pins it.

**The module is staged, not committed, into five build contexts.** Every service but the MCP
server builds from its own directory and so cannot COPY a file above it; `build-deploy.yml`
copies `observability/tracing.py` into each context before `docker build`, and the staged
copies are gitignored. A service added to the Dockerfile list but not the staging list fails
on the COPY; `scripts/test_tracing_build.py` holds the two lists together so that surfaces
as a red test instead.

**`opentelemetry-sdk` is capped at 1.41.1 by google-adk 2.2.0.** Raise it in one service and
that service resolves fine while the *agent* image becomes the one that will not build. The
pin drift guard fails first, and states the cap.

**A trace is not an audit record.** Spans are sampled by policy, dropped on export failure,
and expire on Cloud Trace's retention. Nothing in the controls chain, the loan audit trail or
`turn_signals` depends on one. The trace says where the time went; the governed stores remain
the record of what happened.

## 7. Adding a span

```python
with tracing.span("bq.some_query", client=True, dataset=GOLD_DATASET):
    rows = run_it()
    tracing.set_attrs(rows=len(rows))     # the COUNT, never the rows
```

`client=True` marks an outbound call, which Cloud Trace renders as the caller's half of a
hop. Attributes are keyword arguments, namespaced automatically, and silently dropped if
they are not in `ATTR_KEYS` — so if a new attribute does not appear in Cloud Trace, that is
where to look, and the fix is to add it to the set and to the test that pins it after
checking it against §2.
