variable "project_id" {
  type        = string
  description = "Enterprise host project id."
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "env" {
  type    = string
  default = "enterprise"
}
variable "name_prefix" {
  type    = string
  default = "finchat"
}

# --- Org / identity boundary --------------------------------------------------
variable "org_id" {
  type        = string
  description = "GCP organization id (for org policies + VPC-SC perimeter)."
  default     = ""
}
variable "domain" {
  type        = string
  description = "Workspace/Cloud Identity primary domain (domain-restricted sharing, IAP, CIAM)."
  default     = "datadinosaur.com"
}
variable "access_policy_id" {
  type        = string
  description = "Access Context Manager policy id that owns the VPC-SC perimeter."
  default     = ""
}

# --- Network ------------------------------------------------------------------
variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/20"
}
variable "pods_cidr" {
  type    = string
  default = "10.20.0.0/16"
}
variable "services_cidr" {
  type    = string
  default = "10.30.0.0/20"
}

# --- Multi-region placement ---------------------------------------------------
variable "bq_multi_region" {
  type        = string
  default     = "US"
  description = "BigQuery + GCS multi-region location."
}
variable "spanner_config" {
  type        = string
  default     = "nam3"
  description = "Spanner instance config (nam3 = multi-region US, external consistency)."
}

# --- Cost guardrail -----------------------------------------------------------
variable "enable_budget" {
  type    = bool
  default = true
}
variable "billing_account" {
  type    = string
  default = ""
}
variable "budget_amount_usd" {
  type    = number
  default = 5000
}

variable "labels" {
  type    = map(string)
  default = { app = "finchat", managed_by = "terraform", tier = "enterprise" }
}
