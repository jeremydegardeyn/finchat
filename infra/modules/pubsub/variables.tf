variable "project_id" { type = string }
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "bronze_table" {
  type        = string
  description = "Fully-qualified Bronze table for the BigQuery subscription (project.dataset.table). Empty disables it."
  default     = ""
}

variable "enable_bq_subscription" {
  type        = bool
  description = "Create a native Pub/Sub->BigQuery subscription (cheapest ingest path)."
  default     = true
}

variable "ack_deadline_seconds" {
  type    = number
  default = 60
}

variable "max_delivery_attempts" {
  type    = number
  default = 5
}

variable "labels" {
  type    = map(string)
  default = {}
}
