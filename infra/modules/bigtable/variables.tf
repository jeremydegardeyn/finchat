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

variable "nodes" {
  type        = number
  default     = 1
  description = "Bigtable nodes (no scale-to-zero; ~$475/mo/node — sandbox keeps the module disabled)."
}

variable "reader_members" {
  type        = list(string)
  default     = []
  description = "IAM members with bigtable.reader (the DaaS API SA)."
}

variable "writer_members" {
  type        = list(string)
  default     = []
  description = "IAM members with bigtable.user (pipeline / backfill SA)."
}

variable "labels" {
  type    = map(string)
  default = {}
}
