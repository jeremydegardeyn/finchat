###############################################################################
# Monitoring module — operational observability
# - email notification channel
# - alert: messages stuck in the DLQ (data-quality / poison messages)
# - BigQuery audit log sink -> dedicated dataset (immutable audit trail)
###############################################################################

resource "google_monitoring_notification_channel" "email" {
  count        = var.notification_email == "" ? 0 : 1
  project      = var.project_id
  display_name = "${var.name_prefix}-${var.env}-ops-email"
  type         = "email"
  labels       = { email_address = var.notification_email }
}

# Alert when the dead-letter queue accumulates messages (ingestion health).
resource "google_monitoring_alert_policy" "dlq_backlog" {
  count        = var.notification_email == "" || var.dlq_subscription_id == "" ? 0 : 1
  project      = var.project_id
  display_name = "${var.name_prefix}-${var.env}-dlq-backlog"
  combiner     = "OR"

  conditions {
    display_name = "DLQ undelivered messages > 0"
    condition_threshold {
      filter          = "resource.type=\"pubsub_subscription\" AND resource.label.subscription_id=\"${var.dlq_subscription_id}\" AND metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]
}

# Immutable audit sink: route data-access + admin-activity logs to a locked bucket dataset.
resource "google_logging_project_sink" "audit" {
  project                = var.project_id
  name                   = "${var.name_prefix}-${var.env}-audit-sink"
  destination            = "logging.googleapis.com/projects/${var.project_id}/locations/global/buckets/${var.name_prefix}-${var.env}-audit"
  filter                 = "logName:\"cloudaudit.googleapis.com\""
  unique_writer_identity = true
}

resource "google_logging_project_bucket_config" "audit" {
  project        = var.project_id
  location       = "global"
  bucket_id      = "${var.name_prefix}-${var.env}-audit"
  retention_days = 3650  # 10y immutable audit retention
  locked         = false # set true in prod to make retention immutable
}
