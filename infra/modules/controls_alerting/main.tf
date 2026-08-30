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
  sink_filter = <<-EOT
    jsonPayload.control_event.control_id != ""
    AND jsonPayload.control_event.source != ""
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

resource "google_secret_manager_secret_iam_member" "workflow_reads_secret" {
  count     = local.notify_enabled ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.servicenow[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.workflow[0].email}"
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
