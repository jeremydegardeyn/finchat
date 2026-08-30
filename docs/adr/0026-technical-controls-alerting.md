# ADR-0026 — Technical controls alerting: evidence plane, notification plane, ITSM-owned correlation

- **Status:** Proposed
- **Date:** 2026-08-29
- **Deciders:** Principal Data Architect
- **Context tags:** controls, governance, ITSM, observability, audit, FinOps

## Context

ADR-0008 put Model Armor in the agent request path. It blocks, and it tells the user their
message was blocked. Nobody else finds out. A control that fires silently is indistinguishable
from a control that never fired — which is the question an examiner actually asks.

The immediate requirement is a ServiceNow incident when a Model Armor policy is violated. The
durable requirement is a path that also carries DLP findings and Cloud Composer DAG failures,
and that can answer *prove no violation went unticketed*.

Six findings shaped the decision (full evidence in `docs/26-controls-alerting.md`):

| | Finding |
|---|---|
| F1 | Model Armor has no native alerting. It is a synchronous API — no actions block, no Pub/Sub, no webhook. Everything is downstream of Cloud Logging. |
| F3 | Its sanitize logs **contain the prompt and response**. That is the only place it stores content. |
| F8 | ServiceNow's `sn_em_connector` parses Cloud Monitoring's native JSON 1.2 webhook payload — but it is a Store app and **is not entitled on dev305242**. Event Management *core* is, and `POST /api/now/table/em_event` works (201, `message_key` preserved). |
| F11 | Log-based alerting policies cap at **20 incidents/day per policy**; the excess is dropped. |
| F16 | `armor` is imported in exactly one place — the `/api/agent/*` proxy. Four other model paths reach Vertex through the gateway, unscreened. |
| F18 | dev/test/prod share one project. Environment is a resource label, not a boundary. |

## Decision

**1. Split evidence from notification.**

The *evidence plane* records every control execution — not just violations — as a structured
log entry, sinking to the existing locked 10-year bucket and to BigQuery `control_events`. It
is complete, immutable, and independent of ServiceNow.

The *notification plane* is a log-based alert policy → `webhook_basicauth` → Event Management.
It is how a human finds out, and F11 makes it explicitly lossy.

Conflating the two is what makes controls fail audit: you end up asserting completeness on the
strength of a delivery mechanism that drops messages under exactly the load you care about.

**2. Correlation lives in ServiceNow, not in GCP glue.**

Transport is Pub/Sub → Cloud Workflows → `/api/now/table/em_event`. The Connectors app would
have let Cloud Monitoring's webhook reach Event Management with no code at all, but it is not
entitled here (F8) — and it is only a parser. What it produces is a row we can write directly,
so we lose the free transport and keep every property that motivated the decision. Writing the
payload ourselves is arguably better anyway: the Monitoring webhook would have carried log-entry
content into ServiceNow, which point 4 below forbids.

Emit every event with a `message_key`; Event Management collapses them into one alert and
promotion rules decide what becomes an incident. Airflow retries producing N events for one
failure and a prompt-injection burst producing N events for one attack are the same problem,
and it gets solved once, in the system of record, where `em_alert_management_rule` is readable
by an auditor and changeable without a deploy.

**3. Reconciliation is the control.**

A scheduled comparison of blocked-verdict evidence against incidents raised, joined on
`message_key`. Divergence raises its own incident. This is not defensive garnish — F11
*guarantees* divergence under load, so without it the system quietly under-reports precisely
when it matters.

**4. Redaction by construction, not by discipline.**

The envelope has no free-text field. `control_events.build()` accepts no argument capable of
carrying a prompt, response, or exception message; `filters` is a list of detector names. The
flagged text stays in Model Armor's own sanitize log behind GCP IAM, reachable from the event
via a trace id. Redaction you have to remember to apply is redaction you eventually forget —
and the text Model Armor flags is, by definition, the text you least want in a ticket that a
whole assignment group can read.

**5. Coverage comes from the platform, not from instrumenting agents.**

Three layers, none scaling with agent count: the Model Armor sanitize log is written by the
service and covers every caller (F2); floor settings make screening structural rather than
conventional, closing F16's four unscreened paths without touching an agent; and a DRIFT-4
check in `verify_agent_registry.py` asserts coverage rather than hand-wiring it.

**6. `environment` derives from resource identity, never from the payload.**

The authoritative value is `resource.labels.service_name` — `finchat-prod-ui` — which the
platform stamps on every log entry and the workload cannot forge. (Not `resource.labels.env`, as
first assumed: Cloud Run's monitored resource has a fixed label set, and the custom `env` label
Terraform puts on the service does not propagate to its log entries.) The envelope carries a copy
of `environment` so it is self-describing in BigQuery, but the dispatch workflow derives its own
from the service name and uses the copy only for sources with no service name, such as Composer.
Under F18 this is still a naming convention rather than a project boundary — weaker than a real
deployment, and stated as such rather than papered over.

## Alternatives considered

**A custom Cloud Run bridge doing dedup in Python.** Rejected, and it was the initial design.
It reimplements correlation outside the system of record: an auditor asking which rule decided
twelve violations were one incident gets pointed at a container. If glue is needed at all —
only if Event Management turns out not to be licensed on the PDI — Cloud Workflows is the
right shape: no image, no CI build target, and the `workflows` module already exists.

**Direct log-based alert → ServiceNow Table API.** Zero compute, but the Table API silently
ignores unknown fields, so Monitoring's payload produces a near-empty ticket. It also pipes the
raw log entry — including flagged content — into ServiceNow. Rejected on both counts.

**Security Command Center as the primary path.** AI Protection is GA only in Premium at the
organization level; the 30-day trial auto-converts and would meter all five projects in
`datadinosaur.com`. Deferred. SCC's real value is not duplicating the emitters but assuring
they have no holes — an emitter cannot report traffic that bypassed the emitter. At five agents
`verify_agent_registry.py` proves coverage more cheaply; at 200 agents across 40 projects the
registry only sees teams that registered and SCC becomes load-bearing. That threshold is the
trade, and it is documented rather than guessed.

**Fan out to Teams and ServiceNow independently from GCP.** Creates two correlation domains
that disagree, and the Teams message cannot carry the incident number. Notify from the system
of record instead. The existing `notify_failure_lightweight` Teams ping in the orchestration
repo is retained but re-scoped as break-glass — notification, not record — for when the ITSM
path itself is down, which reconciliation will surface.

## Consequences

**Good.** One envelope serves Model Armor, DLP, Composer, and SCC. DLP policies scale as
config — adding an infoType costs no emission work. Attaching the existing inspect and
de-identify templates via `sdp_settings.advanced_config` gives one PII policy across ingest and
chat, with `FINCHAT_TOKEN` surrogates matching Silver, and makes the chat box a DLP test
harness. Everything sits inside free tiers behind `enable_*` flags defaulted off.

**Costs.** The DLP templates must be regionalized to be referenceable by Model Armor — a
replacement, with the Dataflow reference moving in lockstep. Advanced SDP config adds DLP
inspection billing on top of Model Armor's own per-request price. Event Management on a PDI is
unverified and forks the notification implementation.

**Accepted limits.** The notification plane is lossy by design; reconciliation is what makes
that safe rather than silent. `environment` is a label, not a boundary, until dev/test/prod are
separate projects. The routing matrix in `control_events.ROUTING` is a specification of what
the Event Management rules should do — the rules themselves remain the enforcing artifact.
