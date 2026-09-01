# 26 — Technical Controls Alerting (GCP → ServiceNow)

**Status:** built; awaiting end-to-end verification. A2 (DLP regionalization) applied in
dev/test/prod. A5 infrastructure **deployed in dev** (topic, DLQ, both sinks, workflow, Eventarc
trigger, secret container). A1/A3/A4/A6/A7/A8 written and CI-guarded but inert: `CONTROL_EVENTS`,
`servicenow_instance_url` and `model_armor_use_dlp_templates` all default off, and the dev UI image
predates the emitter until CI rebuilds. Outstanding: end-to-end test, prod/test rollout, SCC
decision. Extends ADR-0008 (Model Armor).
**Related:** ADR-0023 (agent registry), ADR-0024 (AI gateway), `compliance/regulatory-map.md`,
`orchestration` repo (`composer/dags/utils/alerting.py`).

## 1. Problem

A Model Armor policy violation must raise a ServiceNow incident immediately. The same path should
carry other technical-control signals (DLP findings, Cloud Composer DAG failures) and must survive
an audit question: *prove no violation went unticketed.*

Constraint: near-zero sandbox cost. ServiceNow target is a PDI (`dev305242`).

## 2. Verified findings

Checked live against `strongsville-city-schools` and current vendor docs, 2026-08.

| # | Finding | Impact |
|---|---|---|
| F1 | Model Armor has **no native alerting** — no actions block, no Pub/Sub, no webhook. It is a synchronous API. | Any alerting is downstream of Cloud Logging. |
| F2 | With `log_sanitize_operations = true` it writes `type.googleapis.com/google.cloud.modelarmor.logging.v1.SanitizeOperationLogEntry`. | Zero-touch signal covering **every** caller, not just instrumented ones. |
| F3 | Those logs **contain the prompt and response**. It is the only place Model Armor stores content. | Raw entries must never reach a ticket. Keep content in the locked bucket. |
| F4 | DLP **does** have native actions: `pubSubNotification`, `publishSummaryToCscc`, `saveFindings`, Dataplex, email, Cloud Monitoring. Discovery has its own set, including Pub/Sub on sensitivity change. | DLP scales as config, not code. |
| F5 | The DLP Pub/Sub message carries only `DlpJobName`. | Still needs a consumer to fetch findings. |
| F6 | `infra/modules/dlp` creates **global** templates (`parent = projects/P`). Model Armor `advanced_config` requires `projects/P/locations/L/...`. | Templates must be regionalized — a **replacement**, and the Dataflow reference moves with it. |
| F7 | Model Armor `sdp_settings.advanced_config` accepts your own inspect + de-identify templates. With both, it returns de-identified text in `deidentifyResult.data.text`. | One PII policy governs ingest **and** chat; chat can redact-and-continue with `FINCHAT_TOKEN` surrogates matching Silver. |
| F8 | ServiceNow `sn_em_connector` exposes `/api/sn_em_connector/em/inbound_event?source=googlemonitor`, HTTP Basic Auth, role `evt_mgmt_integration`, **JSON 1.2 format only** — which is Cloud Monitoring's native webhook payload. | No translation layer. No custom middleware. |
| F9 | **RESOLVED 2026-08-30.** Event Management *core* activates on dev305242 — `em_event` exists, `evt_mgmt_integration` is assignable, and a POST to `/api/now/table/em_event` returns 201 with `message_key` preserved. **Event Management _Connectors_ does not** — `/api/sn_em_connector/em/inbound_event` returns 400 `"Requested URI does not represent any resource"` (a 400, not a 401: auth succeeded, the namespace is absent). Connectors is a separate ServiceNow Store app requiring entitlement. | The zero-compute webhook path is out. The `em_event` Table API path is in, and it preserves everything that mattered — see §3.1. |
| F10 | Monitoring channel types in this project: `campfire, email, google_chat, hipchat, pagerduty, pubsub, slack, sms, webhook_basicauth, webhook_tokenauth`. **No native MS Teams.** | Teams needs Power Automate, or notify from ServiceNow. |
| F11 | Log-based alerting policies cap at **20 incidents/day per policy**. Excess dropped with a note on the last one. | The notification plane is lossy by design. Reconciliation is mandatory. |
| F12 | `labelExtractors` lift named fields from the log entry into the notification. | The redaction mechanism for the OOTB path. |
| F13 | SCC **AI Protection is GA in Premium at the organization level only.** Standard (free) excludes it. Premium has a 30-day trial that auto-converts to pay-as-you-go. | Org-level activation meters all 5 projects, indefinitely, after day 30. |
| F14 | Org `datadinosaur.com` = `892617109147`; `strongsville-city-schools` sits under it with 4 other projects. | Org-level SCC is possible. |
| F15 | One always-on VM: `instance-20260529-020710`, e2-medium, us-east1-c, tags `http-server,https-server,mysql,phpmyadmin,smtp-outbound` — the DataDinosaur LAMP host. All 17 Cloud Run services are `minScale=0`; `finchat-prod-steward` Cloud SQL is STOPPED. | That VM is effectively the entire SCC pay-as-you-go meter (~1,460 vCPU-hr/mo). It cannot scale to zero. |
| F16 | `armor` is imported in **exactly one place** — `ui/server.py:532`, the `/api/agent/*` proxy. `AI_GATEWAY_URL` appears in five (BFF, loans agents, transactions agent, steward harness, gateway_client). | Four model paths are unscreened today. Per-agent instrumentation would be five integrations and growing. |
| F17 | `gateway_llm.py`: *"Fallback is direct-to-Vertex, and it is counted."* | The gateway is a chokepoint by convention, not enforcement. Floor settings are the enforcing version. |
| F18 | dev/test/prod all live in **one project** (`finchat-dev-*`, `finchat-test-*`, `finchat-prod-*` side by side). | "Prod project vs nonprod project" does not exist here. Environment is a label, not a boundary — a weaker trust signal. |

## 3. Architecture

Two planes, plus a control that binds them.

```
Model Armor --+                        +--> locked log bucket (10y)   [evidence]
DLP           +--> control event ------+--> BQ control_events         [evidence]
Composer    --+    (redacted, no       |
SCC (opt.)  --+     payload content)   +--> Pub/Sub --> Eventarc      [notification]
                                             |              |
                                             +--> DLQ       v
                                                       Cloud Workflows
                                                            |
                                                            v
                                             POST /api/now/table/em_event
                                                            |
                                                       em_alert  (message_key collapses)
                                                            |
                                                  em_alert_management_rule
                                                            |
                                                         incident

              control_events  <-->  incidents
                       +--> reconciliation --> control-failure incident
```

**Evidence plane** — complete, immutable, independent of ServiceNow. Every control *execution*, not
just violations. The system of record for "did the control run and what did it decide."

**Notification plane** — how a human finds out. Best-effort and rate-limited (F11).

### 3.1 Why losing the Connectors app cost almost nothing

The `sn_em_connector` endpoint is a *parser*: it translates a vendor's native alert payload into
an `em_event` row. Its value is that you can point Cloud Monitoring's webhook straight at it and
write no code. Without entitlement, that convenience is gone — but the thing it produces is just
a row in a table we can write ourselves.

So the transport changes and nothing else does. `em_event` still carries `message_key`, Event
Management still collapses events into `em_alert`, `em_alert_management_rule` still decides
promotion, and correlation still lives in the system of record rather than in our code. What we
give up is the zero-compute webhook; what we gain is control of the payload — which we wanted
anyway, because the Monitoring webhook would have carried log-entry content we are obliged to
keep out of ServiceNow (F3).

The one honest cost: a Workflows execution per event, versus none. It stays inside the free tier.

**Reconciliation** — scheduled comparison of blocked-verdict evidence against incidents raised,
joined on `message_key`. Divergence is itself a control failure and raises its own incident. This is
the artifact that answers *prove nothing was dropped*, and it is required precisely because F11
guarantees divergence under load.

### Why correlation lives in ServiceNow

Airflow retries produce N events for one failure; a prompt-injection storm produces N events for one
attack. Same problem. Solving it in GCP glue means an auditor asking "what rule decided these were
one incident?" gets pointed at a container instead of `em_alert_management_rule`. Emit everything
with `message_key`; let Event Management collapse, and let promotion rules decide what becomes an
incident. Correlation belongs in the system of record.

### Canonical envelope

```
control_id, source (model_armor|dlp|composer|scc), environment,
severity, message_key, occurred_at, principal_hash, evidence_ref
```

### Correlation keys on the detector CLASS

`message_key` is `<source>:<control_id>:<principal_hash>:<class>` where class is one of
**security** (prompt injection, malicious URLs), **privacy** (sensitive data), or
**content** (RAI). Highest seriousness wins when several detectors fire.

The first design keyed on the exact filter set and it fragmented the queue in the live prod
test: `Alert0010007 ["pi_and_jailbreak"]` and `Alert0010008 ["pi_and_jailbreak","rai"]` were
the same jailbreak, split because one attempt also tripped RAI; `0009 ["sdp"]` and
`0010 ["rai","sdp"]` split the same way. The set is not stable across attempts of one attack,
so an attacker probing for five minutes opens a fresh ticket every time the detector mix
shifts — the exact flooding correlation exists to prevent. It is the same failure as keying
on an Airflow `try_number`, reached from the other direction: too strict rather than too
lenient. The full filter list still travels in the event, so only the grouping coarsened.

`environment` **must** derive from resource identity — never from a payload field the emitter sets.

Correction to an earlier assumption: it is **not** `resource.labels.env`. Cloud Run's monitored
resource carries a fixed label set (`service_name`, `revision_name`, `location`, `project_id`,
`configuration_name`); the custom `env` label Terraform puts on the *service* does not appear on
its *log entries*. The usable signal is `resource.labels.service_name` — `finchat-prod-ui` — which
is stamped by the platform and equally unforgeable, so the workflow parses the environment out of
the service-name prefix and falls back to the payload copy only for sources that have no service
name (Composer). In a real deployment the project or folder id is the stronger signal (F18).

### Routing as data, not code

| source | env | incident | priority | Teams |
|---|---|---|---|---|
| model_armor / prompt_injection | prod | yes | P2 | via SN |
| model_armor / prompt_injection | dev | no | — | direct |
| composer / dag_failure | prod | yes | P3 | via SN |
| composer / dag_failure | dev | no | — | direct |

All environments emit; EM rules decide promotion. Nonprod keeps visibility as events and alerts
without creating incidents — avoiding the failure where nonprod alerting is switched off and a
staging signal that predicted a prod outage goes unseen. Priority should derive from asset
criticality (`tier` on ontology classes), not be hardcoded per source.

### Coverage: do not instrument per agent

Three layers, none of which scale with agent count:

1. **Platform logs (F2)** — written by the service, not the app. One sink covers every caller that
   will ever exist. This is the layer that scales.
2. **The gateway, not the agents** — one emission point for all five call sites. But F17: it is
   bypassable. **Model Armor floor settings** (`enable_model_armor_floor`, currently off) are the
   enforcing version and close F16's four unscreened paths without touching an agent.
3. **Assert coverage, don't instrument** — add DRIFT-4 to `verify_agent_registry.py`: every
   registered agent must transit the gateway. CI reports the gap; nobody hand-wires an agent.

DLP policies are config: adding an infoType changes detection with zero emission work. Only a new
*scan type* (job trigger or discovery config) needs wiring, and that is declarative TF with native
actions (F4).

### Where SCC fits

SCC is **evidence, not notification** — a findings store with its own lifecycle, reaching the
notification plane only as a producer (continuous export to Pub/Sub). Composer failures are
operational, not security findings; they never touch SCC.

Its value is not duplicating the emitters — it is assurance that the emitters have no holes. An
emitter cannot report traffic that bypassed the emitter; only a platform observer can. At this scale
`verify_agent_registry.py` proves coverage more cheaply. At 200 agents across 40 projects the
registry only sees teams that registered, and SCC becomes load-bearing. That threshold is the trade.

## 4. Plan A — FinChat sandbox build (near-zero cost)

**A0. Fork test (do first, ~10 min).** Activate Event Management on dev305242; `curl` a synthetic
JSON 1.2 payload at the inbound endpoint. Resolves F9 and decides A5.

**A1. Envelope + routing matrix.** *[DONE]* Spec first — both sources must agree on the contract before wiring.

**A2. Regionalize DLP templates.** *[DONE — applied dev/test/prod]* `parent = projects/P/locations/us-central1`; move the Dataflow
reference in lockstep. Replacement, not edit (F6).

**A3. Model Armor `advanced_config`.** *[DONE — flag off]* Point at the regionalized inspect + deid templates. Chat
becomes the DLP test harness with Silver-matching surrogates (F7). Grant the Model Armor service
agent DLP User/Reader.

**A4. Emission + evidence.** *[PARTIAL — emitter + sinks done; floor settings still off]* Floor settings ON (closes F16/F17). Log sink to locked bucket + BQ
`control_events`. Redacted structured control event; `labelExtractors` for the notification (F3/F12).

**A5. Notification.** *[DONE — deployed in dev]* Settled by F9: **Pub/Sub → Cloud Workflows → `/api/now/table/em_event`**.
No container, no CI build target, reuse the existing `workflows` module. Workflows shapes the
envelope into an `em_event` row; ServiceNow still owns correlation and promotion. (Not a Cloud Run
service — that was an earlier, wrong call. Not the `sn_em_connector` webhook — not entitled here.)

**A6. Composer.** *[DONE — orchestration repo, branch controls-alerting-emitter]* `on_failure_callback` emits the same envelope instead of a Teams-shaped message.
Keep `notify_failure_lightweight` as **break-glass**, explicitly scoped as notification not record.

**A7. Reconciliation.** *[DONE]* Scheduled query + control-failure alert. The auditable deliverable.

**A8. Coverage gate.** *[DONE — DRIFT-4, dev+prod clean]* DRIFT-4 in `verify_agent_registry.py`.

**A9. Docs.** ADR-0026, this doc, `docs/08` cost line for DLP inspection under advanced config.

**A10. Optional.** SCC 30-day org trial — **dashboard and evidence only, do not wire to EM** (SCC
finding IDs will not match `message_key`; double-ticketing). Nothing to unwire on day 30.

## 5. Plan B — Enterprise bank reference architecture

Same two-plane shape; every layer hardens.

| Layer | Sandbox (Plan A) | Bank (Plan B) |
|---|---|---|
| Environment boundary | label on shared project (F18) | separate projects under separate folders; env derived from folder/project identity |
| Log aggregation | project sink | **org/folder-level sink** into a dedicated security project |
| Evidence store | BQ + locked bucket, 10y | WORM + legal hold, retention per records schedule, IAM domain separate from the app |
| Connectivity | direct internet webhook to PDI | **MID Server** in a private VPC; no inbound internet path to ServiceNow; Private Service Connect / VPC-SC perimeter |
| Coverage | floor settings + registry CI gate | org-policy-enforced floor settings; SCC Premium org-wide as independent assurance |
| Correlation | EM alert rules | EM + **CMDB**: Service Graph Connector populates CIs; events bind to CIs; assignment and impact from service maps |
| Priority | ontology `tier` | CI criticality × business service impact |
| Change control | none | incident suppression during approved change windows; alert rules under change management |
| Separation of duties | one PDI admin | rule authors ≠ incident closers ≠ evidence custodians |
| SIEM | none | SCC → Google SecOps for cross-signal correlation |
| Reconciliation | scheduled query | formal control with a named owner, quarterly effectiveness testing, evidence retained for exam |
| Model risk | ADR + regulatory map | SR 11-7 / SR 26-2 linkage; the *agent* is in scope, not just the model |
| Resilience | single region | multi-region notification path; documented failure mode when ITSM is down |

**The through-line:** sandbox and bank are the same architecture at different enforcement strengths.
Every sandbox shortcut has a named enterprise counterpart, and the reason for each is cost, not
ignorance — the same dual-tier pattern already used for Apigee→API Gateway and Composer→Workflows.

## 6. Cost and cancellation

Plan A sits inside free tiers: Pub/Sub 10 GiB/mo, Cloud Run scale-to-zero, log routing free, Cloud
Scheduler 3 free jobs. Real line items:

| Item | Cost | Cancel by |
|---|---|---|
| Secret Manager (1 active version) | ~$0.06/mo | delete secret |
| DLP inspection under Model Armor advanced config | per-inspection, negligible at demo volume | revert to `basic_config` |
| Cloud Workflows (only if EM unavailable) | free tier | `enable_*` flag off |
| **SCC Premium org trial** | **$0 for 30 days, then meters all 5 projects** | **deactivate before day 30** |

Everything behind `enable_*` Terraform flags defaulted off — the existing repo pattern. Teardown
order: SCC deactivate → alert policies → sinks → secret → flags off. No committed-use, no reserved
capacity, nothing that survives `terraform destroy`.

The SCC trial is the only irreversible-by-neglect item. F15 is why it matters: the DataDinosaur VM
is the meter and it cannot scale to zero.

## 7. Open forks

1. ~~**F9 — EM on the PDI.**~~ Resolved 2026-08-30: core yes, Connectors no. A5 is settled.
   Remaining sub-question: whether the PDI's Event Management processing jobs actually promote a
   `Ready` event into an `em_alert`, or whether events sit inert. That is a ServiceNow-side
   configuration matter, not an architecture one — the GCP side is identical either way.
2. **SCC trial** — willing; needs a day-25 reminder if taken.
3. **Log-based alert payload** — confirm whether the raw entry rides along with `labelExtractors`,
   or only the extracted labels. Design assumes the safe path (alert on a purpose-built redacted
   entry) so it does not matter.

## 8. Sources

- Model Armor: [logging](https://docs.cloud.google.com/model-armor/configure-logging) · [templates](https://docs.cloud.google.com/model-armor/manage-templates) · [in SCC](https://docs.cloud.google.com/security-command-center/docs/model-armor)
- DLP: [inspection actions](https://docs.cloud.google.com/sensitive-data-protection/docs/concepts-actions) · [discovery actions](https://docs.cloud.google.com/sensitive-data-protection/docs/enable-discovery-actions)
- Monitoring: [notification channels](https://docs.cloud.google.com/monitoring/alerts/using-channels-api) · [log-based alerts](https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts)
- SCC: [service tiers](https://docs.cloud.google.com/security-command-center/docs/service-tiers) · [pricing](https://cloud.google.com/security-command-center/pricing)
- ServiceNow: [GCP events integration](https://www.servicenow.com/docs/r/it-operations-management/event-management/gcp-events-integration.html)

## 9. Runbook — reproducing this from scratch

Recorded as executed, 2026-08-29/30, against `dev305242` and `strongsville-city-schools`.

### 9.1 GCP — regionalize the DLP templates (DONE, all three envs)

`terraform apply` on the whole env would sweep in unrelated drift, including a BigQuery table
replacement that drops rows. Scope every apply:

```
terraform -chdir=infra/envs/<env> init -input=false
terraform -chdir=infra/envs/<env> apply -target=module.dlp -auto-approve
terraform -chdir=infra/envs/<env> apply -refresh-only -auto-approve
```

The second apply is not optional. A targeted apply only refreshes outputs that depend on the
target, so `dlp_location` is missing from state until a refresh runs, and `scripts/run_dataflow.sh`
reads it.

Verify — the value must be locations-qualified, not a bare numeric id:

```
terraform -chdir=infra/envs/<env> output -raw dlp_inspect_template
# projects/<project>/locations/us-central1/inspectTemplates/finchat-<env>-pii-inspect
```

Confirm no Dataflow job is running first (`gcloud dataflow jobs list --status=active`); the
replacement destroys the old templates and anything mid-flight would fail.

### 9.2 ServiceNow — instance access

1. **developer.servicenow.com** → account menu → **Manage instance**.
2. **Log in to instance**, or go to `https://dev305242.service-now.com` as `admin` with the
   instance password shown on that page.
3. PDIs hibernate after ~10 days idle. If you get a wake page, wake it from the portal first.

### 9.3 ServiceNow — activate Event Management

*Manage instance → Activate plugin → Event Management.* Takes 20–40 minutes; it pulls in
dependent plugins and demo data. The Event Management application menu appearing **inside** the
instance is the completion signal, not the portal.

**Event Management Connectors is a separate Store app and is NOT entitled on a PDI.** Confirmed
by probing its endpoint — see 9.5.

### 9.4 ServiceNow — integration user

1. **User Administration → Users** (`sys_user.list`) → **New**
2. User ID `gcp_integration`, **Active** checked. Mark it non-human: on current releases
   that is **Identity type: Machine** plus **Internal Integration User** (older releases
   label the same intent *Web service access only*).
3. **Submit** — the Roles related list only appears after the record exists
4. Reopen → **Roles** → **Edit** → add `evt_mgmt_integration` → Save. That single role is
   all the pipeline needs. Verified against a live instance: it grants write on `em_event`
   and **not** read on `em_alert`, so the integration account cannot see the alerts its own
   events produce — correct least privilege, and worth knowing before you try to verify
   correlation through the API rather than the UI.
5. **Set Password** on the form. If *Password needs reset* is ticked, clear it or Basic Auth
   returns 401.

`evt_mgmt_integration` only exists once Event Management core is active, so its presence in the
role picker is a useful activation check.

### 9.5 Verify the path (the F9 probe)

Connector endpoint — expected to FAIL on a PDI:

```
curl -i -u 'gcp_integration:PASSWORD' -H 'Content-Type: application/json' -d '{}' \
  'https://dev305242.service-now.com/api/sn_em_connector/em/inbound_event?source=googlemonitor'
```

`400 "Requested URI does not represent any resource"` means the Connectors app is absent. Read it
carefully: **400, not 401** — authentication succeeded, only the namespace is missing. A 401 would
mean the user or password is wrong, which is a different problem entirely.

Table API — expected to WORK:

```
curl -i -u 'gcp_integration:PASSWORD' -H 'Content-Type: application/json' \
  -d '{"source":"GCP","event_class":"finchat","resource":"finchat-prod-ui","node":"strongsville-city-schools","type":"model_armor.prompt","severity":"3","message_key":"model_armor:model_armor.prompt:testkey","description":"Model Armor MATCH_FOUND: pi_and_jailbreak"}' \
  'https://dev305242.service-now.com/api/now/table/em_event'
```

`201` with `state: Ready` and `message_key` echoed back. Then within a minute or two,
`https://dev305242.service-now.com/em_alert_list.do` shows an alert — confirming the PDI's event
processing jobs run and correlation is live.

**Prove the correlation.** Re-run the identical curl. `Overall Event Count` on the existing alert
increments instead of a second alert appearing. That single behaviour is what turns a
prompt-injection burst into one incident and three Airflow retries into one; if it does not
increment, the `message_key` is not matching and everything downstream is wrong.

### 9.6 ServiceNow — promotion rule (see §9.7 for why this is the policy boundary)

`em_alert_management_rule.list` → **New**.

- **Name:** `FinChat prod control violations -> Incident`, **Active** checked
- **Conditions:** `Source is GCP` AND `Resource starts with finchat-prod` AND
  `Severity is Major` (2). **Do not match on description text** — the live test found the
  rule silently not firing (`No event rule applied` in the event's Processing Notes) because
  the description format differs between a hand-written test event and the pipeline's.
- **Action:** enable incident creation and set an assignment group

Exact field placement varies by platform release, so locate the incident-creation action on the
rule form rather than following a fixed click path. Before automating, prove the link manually:
open the alert and use its **Create Incident** action. If that produces an incident, the rule is
only automating something already known to work.

### 9.7 Where the policy boundary sits

The dispatch workflow turns facts into a severity (environment plus which detectors fired). The
ServiceNow rule decides which severities deserve an incident. That split is deliberate: GCP owns
what happened, ServiceNow owns what to do about it, and the promotion threshold can be retuned by
an ITOM admin without a deploy.

The rule filters on `Resource starts with finchat-prod` as well as severity even though severity
already encodes the environment. The redundancy is for the reader: an auditor examining the rule
can see the prod intent without reverse-engineering the severity mapping.

## 10. Operational notes found during rollout

**CI/CD owns the Cloud Run environment, so a hand-set flag does not survive a deploy.**
`build-deploy.yml` passes the full env with `--set-env-vars`, which replaces rather than merges.
`CONTROL_EVENTS=1` set with `gcloud run services update` was silently dropped by the very deploy
that shipped the emitter — the module arrived and the flag left on the same revision. This is the
same clobbering hazard the `cloud_run` Terraform module works around with `ignore_changes`, with
CI/CD as the actor instead of Terraform. The flag is now sourced from the `CONTROL_EVENTS` GitHub
Actions variable (default `0`), so it is set once per environment and survives.

**Two CI permissions were missing and only surfaced under load.** Basic `roles/viewer` covers
`*.get` and `*.list` but not `*.getIamPolicy`, so `terraform plan` 403s on any
`google_*_iam_member` — latent until this work added the first such binding CI had to refresh.
Fixed with `roles/iam.securityReviewer` (read-only), applied to dev, test and prod.

Separately, the platform-docs corpus step fails with `bigquery.tables.create denied on
finchat_kb_dev`. It is `continue-on-error: true` by design ("never block a deploy on the search
corpus") so it does not stop a release, but it means **new documentation — including this file —
is not searchable in the platform KB** until the CI/CD service account gets dataset-level write on
`finchat_kb_<env>`. Unrelated to controls alerting; recorded here because this rollout is what
surfaced it.

## 11. What the live end-to-end test found

Run against dev and prod on 2026-08-30. The pipeline worked first time; everything below is
a defect the test surfaced that unit tests could not.

**The UI swallowed the block and answered anyway.** A prompt carrying an SSN was screened
out, the control event fired, the workflow ran, ServiceNow raised the ticket — and the SPA
showed an account balance. `api()` only special-cased 503, so a 400 refusal arrived as an
ordinary object with `.error` set, indistinguishable from a backend outage; `agentAnswer()`
treated it as one and fell through to `groundedAnswer()`, which matched `/balance/` and
served the answer from the client-side path. The screening decision was made, recorded,
ticketed, and then ignored by the only component a user can see — the worst shape a control
failure can take, because everything downstream looks healthy. Fixed; guarded by
`ui/test_block_surfacing.py`.

**Every event landed at midnight.** ServiceNow date fields want `YYYY-MM-DD HH:MM:SS`; handed
the ISO-8601 string the envelope carries, the parser takes the date and zeroes the time. No
error, row created, chronology gone. Fixed in the workflow and pinned by tests.

**Correlation was too strict** — see §3, class-based keying.

**ServiceNow writes ~3 `em_event` rows per POST.** Verified one workflow execution returning
`status 201` per event, so this is not duplicate sending on our side; the extra rows appear
during EM's own processing. They collapse into a single `em_alert`, so it is cosmetic at the
alert layer — but a row count is not an event count, and any reporting built on `em_event`
must account for it.

**Nothing promoted to an incident.** The event record's Processing Notes read
`No event rule applied`. The alert management rule's conditions do not match what the
pipeline emits — a rule keyed on the description text of a hand-written test event
(`Model Armor MATCH_FOUND: pi_and_jailbreak`) will miss the pipeline's format
(`model_armor match ["sdp"]`). Match on `Source`, `Resource` and `Severity` instead, which
are stable.

**No CI binding.** `Failed to find the host with name: strongsville-city-schools` — there is
no CMDB in this sandbox, so alerts cannot bind to a configuration item and assignment stays
manual. This is exactly the Plan B row (§5) where a real deployment diverges: CI binding is
what drives auto-assignment, impact and service maps.

**The integration user cannot read its own alerts.** `evt_mgmt_integration` grants write on
`em_event` and not read on `em_alert`. Correct least privilege; it means correlation must be
verified in the UI, not through the API.

## 12. CI binding — using FinChat as the configuration item

Every event carried `No CI found for binding — Failed to find the host with name:
strongsville-city-schools`, and no alert promoted to an incident. I predicted those were the same
fact — that the Create Incident subflow needed a CI to attach to.

**Tested, and that prediction was wrong.** With `cmdb_ci` set explicitly the binding succeeds
(`Alert CI will be bound to CI id … Bind to …`), a fresh alert is created with the CI attached at
severity 2 matching every rule condition including `incidentISEMPTY` — **and `incident` is still
empty.** CI binding and incident promotion are independent problems. The CMDB work below is
worth doing on its own merits; it is not what unblocks promotion.

### Node binding needs more than a matching CI

Creating a `cmdb_ci_appl` named `strongsville-city-schools` was not enough: Event Management
resolves `node` against **host** classes and kept logging `Failed to find the host with name`.
Setting `ci_type: cmdb_ci_appl` on the event made the class register (`Event CI type is
cmdb_ci_appl`) but did not change the lookup — node binding still went looking for a host.

Only an explicit `cmdb_ci` on the event bound successfully. So either the CIs are created in a
host class so `node` resolves naturally, or the event carries the CI sys_id directly — which
means the workflow needs it from configuration, since a sys_id is instance-specific.

### The choice, and why node stays the project

Event Management resolves the event's `node` field against the CMDB as a host name. Two ways to
make that resolve:

**(a) Change `node` to the Cloud Run service** (`finchat-prod-ui`) and create CIs with those names.
Rejected: `resource` already carries the service, so `node` would duplicate it, and the emitting
project — the one fact that identifies the tenant — would be pushed into `additional_info` where
no correlation can reach it.

**(b) Create a CI for the GCP project and let `node` resolve to it.** Chosen. It needs no change
to the dispatch workflow, and it is what the field already means: a GCP project *is* a
configuration item, and in a real cloud CMDB it is modelled as a cloud service account with the
services running on it as children.

### The records to create

| CI | Class | Name |
|---|---|---|
| GCP project | `cmdb_ci_cloud_service_account` (or `cmdb_ci_appl`) | `strongsville-city-schools` |
| BFF, per env | `cmdb_ci_appl` | `finchat-dev-ui`, `finchat-test-ui`, `finchat-prod-ui` |

The service CIs are children of the project CI, so an alert binding on `node` reaches the project
and the `resource` field still names the specific service. Assignment can then derive from CI
ownership rather than the hardcoded group the alert rule uses today — which is exactly the
Plan B row in §5, made concrete.

### What this is worth saying in the write-up

The sandbox has no CMDB because nothing populated one; a bank has Service Graph Connectors
discovering GCP continuously. The gap is not that CI binding is hard, it is that **an ITSM
integration is only as good as the asset inventory underneath it** — without a CI, an incident
cannot route itself, and every alert lands on whichever group someone hardcoded. That is the real
argument for the catalog and ontology work in Inc 10 and Inc 20 sitting under all of this.

### Promotion is blocked by something else

Ruled out by testing, in order: the rule's conditions (it matches on Source/Resource/Severity,
all verified against a live alert), the rule's own state (`active`, `state=1`, `type=incident`,
copied field-for-field from the working OOB rule), the remediation subflow (`Create Incident` is
`active`, `published`, `run_as: system`), and CI binding (proven to work independently, with no
effect on promotion).

What remains untested is whether this PDI evaluates alert management rules automatically at all.
The manual **Quick Incident** action on the alert form works — that is how INC0010001 was created
— so the alert-to-incident path itself is sound; only the automatic trigger is not firing.

Two ways forward, and they are a real choice rather than a workaround:

1. **A Business Rule on `em_alert`** that creates an incident when the conditions match. Fully
   supported, needs no ITOM Pro flow engine, and keeps promotion inside ServiceNow — so the
   architectural principle in ADR-0026 (correlation and promotion live in the system of record)
   still holds. It trades a declarative rule for a script.
2. **Leave promotion manual** and say so. Every alert is present, correlated and CI-bound; a human
   clicks Quick Incident. For a sandbox demonstrating the *pipeline*, that may be honest enough —
   and it is what the reconciliation control exists to catch if anyone forgets.
