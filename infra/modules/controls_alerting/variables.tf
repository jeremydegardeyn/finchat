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

variable "chat_provider" {
  type        = string
  description = <<-EOT
    Post each dispatched control event to a chat channel as well: "teams", "google_chat",
    or "" for none.

    Either way this is the SECOND-best shape. Notifying from ServiceNow would keep one
    correlation domain and let the message carry its incident number; the Teams spoke needs
    an entitlement this PDI lacks, exactly as the Event Management Connectors app did.
    Posting from the workflow means chat and ServiceNow count separately — the card links
    back to the event record so a responder can cross over, but the two can disagree under
    load, and only ServiceNow is the record.

    "teams" needs a Power Automate Workflows webhook, which requires a work or school
    Microsoft 365 account; a personal account cannot create one, and the free E5 developer
    sandbox now requires a Visual Studio Professional/Enterprise subscription. "google_chat"
    needs only a Workspace space, which is why it is the sandbox default while Teams stays
    documented for the enterprise story.

    Terraform creates the secret container; add the webhook URL out of band. A chat webhook
    URL is a credential — anyone holding it can post to the channel.
  EOT
  default     = ""
  validation {
    condition     = contains(["", "teams", "google_chat"], var.chat_provider)
    error_message = "chat_provider must be \"teams\", \"google_chat\", or \"\"."
  }
}

variable "evidence_dataset" {
  type        = string
  description = "BigQuery dataset for the append-only control_events evidence table."
  default     = ""
}
