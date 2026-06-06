###############################################################################
# Dataflow module — Apache Beam streaming, run on-demand by default.
#
# Near-zero-cost posture (ADR-0003): the pipeline is packaged as a Flex Template
# and launched per generation run, then drained -> no idle workers.
# Set enable_streaming_job = true to deploy the SAME template as a persistent
# 24/7 streaming job (the enterprise mapping). No pipeline code changes.
###############################################################################

locals {
  job_name = "${var.name_prefix}-${var.env}-txn-stream"
}

resource "google_dataflow_flex_template_job" "streaming" {
  count    = var.enable_streaming_job && var.template_spec_path != "" ? 1 : 0
  provider = google-beta

  project                 = var.project_id
  region                  = var.region
  name                    = local.job_name
  container_spec_gcs_path = var.template_spec_path

  parameters = {
    input_subscription = var.input_subscription
    output_table       = var.silver_transaction_table
    dlq_topic          = var.dlq_topic
    temp_location      = "gs://${var.dataflow_bucket}/temp"
    staging_location   = "gs://${var.dataflow_bucket}/staging"
  }

  service_account_email = var.pipeline_service_account
  max_workers           = var.max_workers
  num_workers           = 1
  on_delete             = "drain"
  labels                = var.labels
}
