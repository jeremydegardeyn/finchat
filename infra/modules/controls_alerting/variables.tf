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

variable "enable_teams_notify" {
  type        = bool
  description = <<-EOT
    Also post each dispatched control event to a Microsoft Teams channel.

    This is the SECOND-best shape and is chosen because the first is unavailable. Notifying
    from ServiceNow would keep one correlation domain and let the message carry its incident
    number; the Teams spoke needs an entitlement this PDI does not have, the same way the
    Event Management Connectors app did. Posting from the workflow instead means Teams and
    ServiceNow count separately — the Teams message links back to the event record so a
    responder can still cross over, but the two can disagree under load, and only ServiceNow
    is the record.

    Terraform creates the secret container; add the webhook URL out of band. A Teams webhook
    URL is a credential — anyone holding it can post to the channel.
  EOT
  default     = false
}

variable "evidence_dataset" {
  type        = string
  description = "BigQuery dataset for the append-only control_events evidence table."
  default     = ""
}
