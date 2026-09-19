###############################################################################
# Controls alerting (ADR-0026) — two planes off one emitted event.
#
#   control-event log line (ui/control_events.py, Composer on_failure_callback)
#        |
#        +-- sink -> BigQuery control_events        [evidence: complete, queryable]
#        +-- sink -> Pub/Sub -> Eventarc -> Workflows -> ServiceNow em_event
#                                                       [notification: best-effort]
#
# The evidence sink is the system of record and runs whether or not ServiceNow is
# reachable, licensed, or awake. Reconciliation between the two is what makes this a
# control rather than a pipeline — see docs/26 §3.
#
# Everything is off by default: no topic, no workflow, no secret until the caller sets
# the flags. Near-zero cost is a property of the defaults, not of good intentions.
###############################################################################

locals {
  prefix = "${var.name_prefix}-${var.env}"

  # Selects ONLY the redacted envelope emitted by our own control points. Model Armor's
  # raw sanitize logs match none of this by design — they carry the flagged prompt and
  # never leave GCP (docs/26 F3).
  #
  # The environment predicate is not optional. A log sink is scoped to a PROJECT, and this
  # project holds dev, test and prod, so without it all three sinks match every control
  # event: one violation became three workflow executions and three em_event rows, and
  # each environment's evidence dataset accumulated the other two environments' events
  # (docs/26 F19). It stayed invisible because all three rows carry the same message_key
  # and Event Management correlated them into a single alert; only the chat plane, which
  # has no correlation, showed the duplication. Reconciliation cannot catch it either —
  # both sides are triplicated, so they agree.
  #
  # Discriminate on resource identity, matching how the workflow derives `environment`:
  # the Cloud Run service name is stamped by the platform and the workload cannot forge
  # it, whereas the copy in the payload is emitter-set and only advisory (docs/26 F18).
  # Sources with no service_name — Composer, chiefly — fall back to the payload value,
  # which is the same trade the workflow makes.
  sink_filter = <<-EOT
    jsonPayload.control_event.control_id != ""
    AND jsonPayload.control_event.source != ""
    AND (
      resource.labels.service_name =~ "^${local.prefix}-"
      OR (
        NOT resource.labels.service_name:*
        AND jsonPayload.control_event.environment = "${var.env}"
      )
    )
  EOT

  notify_enabled = var.servicenow_instance_url != ""
}

# --- notification plane ------------------------------------------------------

resource "google_pubsub_topic" "events" {
  count   = local.notify_enabled ? 1 : 0
  project = var.project_id
  name    = "${local.prefix}-control-events"
}

# Poison messages park here instead of retrying against ServiceNow forever. A PDI that
# has hibernated looks exactly like an outage, and this is what stops that from becoming
# an unbounded retry storm.
resource "google_pubsub_topic" "dlq" {
  count   = local.notify_enabled ? 1 : 0
  project = var.project_id
  name    = "${local.prefix}-control-events-dlq"
}

resource "google_logging_project_sink" "notify" {
  count                  = local.notify_enabled ? 1 : 0
  project                = var.project_id
  name                   = "${local.prefix}-control-events-notify"
  destination            = "pubsub.googleapis.com/projects/${var.project_id}/topics/${google_pubsub_topic.events[0].name}"
  filter                 = local.sink_filter
  unique_writer_identity = true
}

resource "google_pubsub_topic_iam_member" "sink_writer" {
  count   = local.notify_enabled ? 1 : 0
  project = var.project_id
  topic   = google_pubsub_topic.events[0].name
  role    = "roles/pubsub.publisher"
  member  = google_logging_project_sink.notify[0].writer_identity
}

# --- ServiceNow credentials --------------------------------------------------
# Terraform owns the secret CONTAINER, not the value. The password is added out of band
# (`gcloud secrets versions add`) so it never lands in state, a plan output, or a diff.

resource "google_secret_manager_secret" "servicenow" {
  count     = local.notify_enabled ? 1 : 0
  project   = var.project_id
  secret_id = "${local.prefix}-servicenow-auth"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "chat" {
  count     = local.notify_enabled && var.chat_provider != "" ? 1 : 0
  project   = var.project_id
  secret_id = "${local.prefix}-chat-webhook"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "workflow_reads_chat" {
  count     = local.notify_enabled && var.chat_provider != "" ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.chat[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workflow[0].email}"
}

resource "google_secret_manager_secret_iam_member" "workflow_reads_secret" {
  count     = local.notify_enabled ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.servicenow[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workflow[0].email}"
}

# The fallback leg writes a log entry (workflow.yaml, log_undelivered) and the alert
# policy below reads it. Found live: the first undelivered event died here with
# `logging.logEntries.create` denied — the SA had only ever needed ServiceNow and two
# secrets. A fallback that cannot record the failure it exists for is not a fallback.
resource "google_project_iam_member" "workflow_writes_logs" {
  count   = local.notify_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.workflow[0].email}"
}

# --- workflow ----------------------------------------------------------------

resource "google_service_account" "workflow" {
  count        = local.notify_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${local.prefix}-controls-wf"
  display_name = "Controls alerting workflow (ADR-0026)"
}

resource "google_workflows_workflow" "dispatch" {
  count           = local.notify_enabled ? 1 : 0
  project         = var.project_id
  region          = var.region
  name            = "${local.prefix}-controls-dispatch"
  service_account = google_service_account.workflow[0].id
  source_contents = file("${path.module}/workflow.yaml")

  user_env_vars = {
    SN_INSTANCE_URL = var.servicenow_instance_url
    SN_USER         = var.servicenow_user
    SN_SECRET_ID    = google_secret_manager_secret.servicenow[0].secret_id
    # Empty disables the chat hop entirely — the workflow skips it rather than failing.
    CHAT_SECRET_ID = var.chat_provider == "" ? "" : google_secret_manager_secret.chat[0].secret_id
    CHAT_PROVIDER  = var.chat_provider
  }
}

resource "google_service_account" "trigger" {
  count        = local.notify_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${local.prefix}-controls-trig"
  display_name = "Eventarc trigger for controls alerting"
}

resource "google_project_iam_member" "trigger_invokes_workflow" {
  count   = local.notify_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.trigger[0].email}"
}

resource "google_eventarc_trigger" "on_event" {
  count           = local.notify_enabled ? 1 : 0
  project         = var.project_id
  location        = var.region
  name            = "${local.prefix}-controls-trigger"
  service_account = google_service_account.trigger[0].email

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = google_pubsub_topic.events[0].id
    }
  }

  destination {
    workflow = google_workflows_workflow.dispatch[0].id
  }

  depends_on = [google_project_iam_member.trigger_invokes_workflow]
}

# --- evidence plane ----------------------------------------------------------
# Deliberately independent of everything above: it keeps recording when ServiceNow is
# down, unlicensed, or hibernating, which is the whole point of separating the planes.

# NOTE: there is deliberately no google_bigquery_table here.
#
# A Cloud Logging BigQuery sink names its own destination tables after the log id — the
# control events land in `run_googleapis_com_stdout`, not in anything we choose. An
# earlier revision of this module declared a `control_events` table, which Terraform
# would have created and Logging would then have ignored: an empty table sitting beside
# the real data, looking authoritative in every diff.
#
# The queryable `control_events` view over the sink's table belongs with the
# reconciliation work (A7), because it cannot be created until the sink's table exists,
# and that only happens once the first event is written.

resource "google_logging_project_sink" "evidence" {
  count                  = var.evidence_dataset == "" ? 0 : 1
  project                = var.project_id
  name                   = "${local.prefix}-control-events-evidence"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${var.evidence_dataset}"
  filter                 = local.sink_filter
  unique_writer_identity = true

  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "evidence_writer" {
  count      = var.evidence_dataset == "" ? 0 : 1
  project    = var.project_id
  dataset_id = var.evidence_dataset
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.evidence[0].writer_identity
}

# --- fallback: email when ServiceNow did not take the event ------------------
# The workflow logs a `control_event_undelivered` entry when the em_event POST fails
# (workflow.yaml, post_event/except). This log-based alert policy matches that entry and
# emails the address in `alert_email`. It is the one leg of the notification plane that
# needs no ITSM, no webhook secret and no third-party mail API — which is the point of a
# fallback. Two known properties of this path, both from docs/26: it is rate-limited
# (F11 — one notification per `period`, and at most 20 incidents a day per policy) and
# it carries only what labelExtractors lift from the entry (F12) — envelope metadata and
# the transport error, never content. Off unless `alert_email` is set.

resource "google_monitoring_notification_channel" "fallback_email" {
  count        = local.notify_enabled && var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${local.prefix} controls fallback (email)"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_alert_policy" "undelivered" {
  count        = local.notify_enabled && var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${local.prefix}: control event not delivered to ServiceNow"
  combiner     = "OR"

  conditions {
    display_name = "control_event_undelivered logged by the dispatch workflow"
    condition_matched_log {
      filter = <<-EOT
        resource.type="workflows.googleapis.com/Workflow"
        AND resource.labels.workflow_id="${local.prefix}-controls-dispatch"
        AND jsonPayload.control_event_undelivered.control_id:*
      EOT
      label_extractors = {
        control_id     = "EXTRACT(jsonPayload.control_event_undelivered.control_id)"
        source         = "EXTRACT(jsonPayload.control_event_undelivered.source)"
        severity       = "EXTRACT(jsonPayload.control_event_undelivered.severity)"
        environment    = "EXTRACT(jsonPayload.control_event_undelivered.environment)"
        message_key    = "EXTRACT(jsonPayload.control_event_undelivered.message_key)"
        principal_hash = "EXTRACT(jsonPayload.control_event_undelivered.principal_hash)"
        filters        = "EXTRACT(jsonPayload.control_event_undelivered.filters)"
        sn_error       = "EXTRACT(jsonPayload.control_event_undelivered.sn_error)"
      }
    }
  }

  alert_strategy {
    # Minimum the API allows. A probing session emits one event per flagged turn; the
    # log entries are all kept (evidence), but one email per five minutes is enough.
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.fallback_email[0].id]

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      **ServiceNow did not take a control event** — this email is the fallback leg.

      - control: `$${log.extracted_label.control_id}` (source `$${log.extracted_label.source}`)
      - severity: `$${log.extracted_label.severity}` · environment: `$${log.extracted_label.environment}`
      - detectors: `$${log.extracted_label.filters}`
      - correlation key: `$${log.extracted_label.message_key}`
      - principal (pseudonymous): `$${log.extracted_label.principal_hash}`
      - ServiceNow error: `$${log.extracted_label.sn_error}`

      The event is recorded in BigQuery `control_events` and, for `conversation_safety`,
      the session trajectory is in `finchat_eval_<env>.session_trajectory`. No message
      content travels in this alert by design (docs/26, ADR-0026 rule 1; ADR-0034).
    EOT
  }
}
