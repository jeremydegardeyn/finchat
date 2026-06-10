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

variable "table_expiration_days" {
  type        = number
  description = "Default partition expiration (days) for cost control. Bronze events."
  default     = 400
}

variable "viewer_members" {
  type        = list(string)
  description = "IAM members granted dataViewer on Gold (e.g., DaaS API SA)."
  default     = []
}

variable "editor_members" {
  type        = list(string)
  description = "IAM members granted dataEditor on Bronze/Silver (e.g., pipeline SA)."
  default     = []
}

variable "privileged_group" {
  type        = string
  description = "Group/member allowed to read unmasked PII (fine-grained reader on ALL policy tags)."
  default     = ""
}

variable "financial_reader_members" {
  type        = list(string)
  description = "Members granted fine-grained read on PII_FINANCIAL only (e.g. the DaaS API SA that serves balances)."
  default     = []
}

variable "eval_writer_members" {
  type        = list(string)
  description = "Members granted dataEditor on the live-eval dataset (BFF writes logs; scorer writes scores)."
  default     = []
}

variable "labels" {
  type    = map(string)
  default = {}
}
