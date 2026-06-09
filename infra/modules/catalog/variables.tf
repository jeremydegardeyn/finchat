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

variable "silver_dataset" {
  type        = string
  description = "Silver dataset id (for DQ scan targets)."
}

variable "policy_tag_ids" {
  type        = map(string)
  default     = {}
  description = <<-EOT
    Map of classification -> policy tag id (from the bigquery module). The DQ/
    profile scans read policy-tag-protected columns across the products, so the
    Dataplex scan service agent is granted fine-grained reader on each. Empty to
    skip the grants.
  EOT
}

variable "profile_targets" {
  type = list(object({
    id      = string
    dataset = string
    table   = string
  }))
  default     = []
  description = "Products to data-profile (Insights). One profile datascan per entry."
}

variable "domains" {
  type        = list(string)
  description = "Business domains -> one catalog Entry Group each."
  default     = ["customer", "deposits", "lending", "payments", "fraud", "risk", "treasury", "marketing"]
}

variable "labels" {
  type    = map(string)
  default = {}
}
