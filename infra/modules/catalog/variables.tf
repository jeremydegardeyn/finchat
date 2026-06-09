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

variable "domains" {
  type        = list(string)
  description = "Business domains -> one catalog Entry Group each."
  default     = ["customer", "deposits", "lending", "payments", "fraud", "risk", "treasury", "marketing"]
}

variable "labels" {
  type    = map(string)
  default = {}
}
