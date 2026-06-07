###############################################################################
# Pub/Sub module — event-driven ingestion backbone
# - transactions ingest topic (+ optional Avro schema)
# - dead-letter topic + subscription (DLQ for poison messages)
# - native BigQuery subscription (cheapest path to Bronze)
# - pull subscription for the Dataflow streaming pipeline
###############################################################################

locals {
  prefix = "${var.name_prefix}-${var.env}"
}

# --- Message schema (governance: schema enforcement at the edge) -------------
resource "google_pubsub_schema" "transaction" {
  name = "${local.prefix}-transaction"
  type = "AVRO"
  definition = jsonencode({
    type = "record"
    name = "Transaction"
    fields = [
      { name = "transaction_id", type = "string" },
      { name = "idempotency_key", type = "string" },
      { name = "account_id", type = "string" },
      { name = "txn_type", type = "string" },
      { name = "amount", type = "string" },
      { name = "currency", type = "string" },
      # Plain string (not a union): Avro-JSON unions require {"string": x} wrappers,
      # which breaks normal JSON publishers. Empty string "" means "no counterparty".
      { name = "counterparty_account", type = "string", default = "" },
      { name = "status", type = "string" },
      { name = "event_time", type = "string" },
    ]
  })
}

# --- Topics ------------------------------------------------------------------
resource "google_pubsub_topic" "ingest" {
  project = var.project_id
  name    = "${local.prefix}-transactions-ingest"
  labels  = var.labels

  schema_settings {
    schema   = google_pubsub_schema.transaction.id
    encoding = "JSON"
  }
  message_retention_duration = "86400s"
}

resource "google_pubsub_topic" "dlq" {
  project = var.project_id
  name    = "${local.prefix}-transactions-dlq"
  labels  = var.labels
}

# --- Dead-letter subscription (for inspection / replay) ----------------------
resource "google_pubsub_subscription" "dlq" {
  project = var.project_id
  name    = "${local.prefix}-transactions-dlq-sub"
  topic   = google_pubsub_topic.dlq.id
  labels  = var.labels

  message_retention_duration = "604800s" # 7 days for triage
  expiration_policy { ttl = "" }
}

# --- BigQuery subscription (native, scale-to-zero, cheapest Bronze path) ------
resource "google_pubsub_subscription" "to_bigquery" {
  count   = var.enable_bq_subscription && var.bronze_table != "" ? 1 : 0
  project = var.project_id
  name    = "${local.prefix}-transactions-to-bq"
  topic   = google_pubsub_topic.ingest.id
  labels  = var.labels

  bigquery_config {
    table            = var.bronze_table
    use_topic_schema = false # raw payload lands in `data`; immutable Bronze (ADR-0001)
    write_metadata   = true  # adds subscription_name, message_id, publish_time, attributes -> lineage
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = var.max_delivery_attempts
  }
}

# --- Pull subscription for the Dataflow streaming pipeline --------------------
resource "google_pubsub_subscription" "dataflow" {
  project              = var.project_id
  name                 = "${local.prefix}-transactions-dataflow"
  topic                = google_pubsub_topic.ingest.id
  ack_deadline_seconds = var.ack_deadline_seconds
  labels               = var.labels

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = var.max_delivery_attempts
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  expiration_policy { ttl = "" }
}
