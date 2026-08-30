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

variable "servicenow_instance_url" {
  type        = string
  description = "e.g. https://dev305242.service-now.com. Empty disables the notification path (the evidence plane still runs)."
  default     = ""
}

variable "servicenow_user" {
  type        = string
  description = "Integration user with evt_mgmt_integration. Never an admin account."
  default     = "gcp_integration"
}

variable "evidence_dataset" {
  type        = string
  description = "BigQuery dataset for the append-only control_events evidence table."
  default     = ""
}

variable "evidence_retention_days" {
  type        = number
  description = "Partition expiry on control_events. Null keeps rows forever; the locked log bucket is the long-retention copy either way."
  default     = null
}
