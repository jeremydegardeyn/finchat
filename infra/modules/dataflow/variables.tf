variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "dataflow_bucket" {
  type        = string
  description = "GCS bucket for temp/staging + Flex Template spec (from foundation module)."
}

variable "template_spec_path" {
  type        = string
  description = "GCS path to the built Flex Template spec JSON (gs://.../template.json)."
  default     = ""
}

variable "pipeline_service_account" {
  type        = string
  description = "Service account email the Dataflow workers run as."
}

variable "input_subscription" {
  type        = string
  description = "Pub/Sub subscription the pipeline reads."
}

variable "silver_transaction_table" {
  type        = string
  description = "Destination Silver table (project:dataset.table)."
}

variable "dlq_topic" {
  type        = string
  description = "Dead-letter topic for unparseable/invalid records."
}

# COST TOGGLE: false (sandbox) = run on-demand & drain; true (enterprise) = 24/7 streaming job.
variable "enable_streaming_job" {
  type        = bool
  description = "Deploy a persistent 24/7 streaming Dataflow job (enterprise). Default off for near-zero cost."
  default     = false
}

variable "max_workers" {
  type    = number
  default = 2
}

variable "labels" {
  type    = map(string)
  default = {}
}
