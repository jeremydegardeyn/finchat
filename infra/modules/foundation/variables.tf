variable "project_id" {
  type        = string
  description = "GCP project ID (sandbox: strongsville-city-schools)."
}

variable "region" {
  type        = string
  description = "Primary region for regional resources."
  default     = "us-central1"
}

variable "env" {
  type        = string
  description = "Environment short name (dev|test|prod)."
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix."
  default     = "finchat"
}

variable "enable_budget" {
  type        = bool
  description = "Create a Cloud Billing budget + alert. Requires billing_account."
  default     = false
}

variable "billing_account" {
  type        = string
  description = "Billing account ID for the budget (format: XXXXXX-XXXXXX-XXXXXX)."
  default     = ""
}

variable "budget_amount_usd" {
  type        = number
  description = "Monthly budget amount in USD; alerts fire at 50/90/100%."
  default     = 25
}

variable "labels" {
  type        = map(string)
  description = "Common resource labels."
  default     = {}
}
