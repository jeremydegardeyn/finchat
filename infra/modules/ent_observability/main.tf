# Observability: audit-log sink to BigQuery, a monitoring dashboard, an SLO with a
# fast-burn alert, and an uptime check. Reference overlay — not applied.

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "domain" { type = string }

# Long-term, queryable audit trail: Cloud Audit Logs -> BigQuery.
resource "google_bigquery_dataset" "logs" {
  project    = var.project_id
  dataset_id = "${var.name_prefix}_logs_${var.env}"
  location   = "US"
}

resource "google_logging_project_sink" "audit" {
  project                = var.project_id
  name                   = "${var.name_prefix}-${var.env}-audit-sink"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.logs.dataset_id}"
  filter                 = "logName:\"cloudaudit.googleapis.com\""
  unique_writer_identity = true
}

resource "google_bigquery_dataset_iam_member" "sink_writer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.logs.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.audit.writer_identity
}

resource "google_monitoring_dashboard" "main" {
  project = var.project_id
  dashboard_json = jsonencode({
    displayName = "FinChat Enterprise"
    gridLayout = {
      widgets = [
        {
          title = "Request latency p95"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "metric.type=\"loadbalancing.googleapis.com/https/total_latencies\""
                  aggregation = {
                    alignmentPeriod  = "60s"
                    perSeriesAligner = "ALIGN_PERCENTILE_95"
                  }
                }
              }
            }]
          }
        }
      ]
    }
  })
}

resource "google_monitoring_uptime_check_config" "ui" {
  project      = var.project_id
  display_name = "${var.name_prefix}-${var.env}-ui-uptime"
  timeout      = "10s"
  period       = "60s"
  http_check {
    path    = "/healthz"
    port    = 443
    use_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      host       = "app.${var.domain}"
      project_id = var.project_id
    }
  }
}

resource "google_monitoring_custom_service" "ui" {
  project      = var.project_id
  service_id   = "${var.name_prefix}-${var.env}-ui"
  display_name = "FinChat UI"
}

resource "google_monitoring_slo" "availability" {
  project             = var.project_id
  service             = google_monitoring_custom_service.ui.service_id
  slo_id              = "availability-99-9"
  display_name        = "99.9% availability (28d)"
  goal                = 0.999
  rolling_period_days = 28
  basic_sli {
    availability {}
  }
}

resource "google_monitoring_alert_policy" "fast_burn" {
  project      = var.project_id
  display_name = "${var.name_prefix}-${var.env} SLO fast burn"
  combiner     = "OR"
  conditions {
    display_name = "LB p95 latency > 2s"
    condition_threshold {
      filter          = "metric.type=\"loadbalancing.googleapis.com/https/total_latencies\" resource.type=\"https_lb_rule\""
      comparison      = "COMPARISON_GT"
      threshold_value = 2000
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_95"
      }
    }
  }
}
