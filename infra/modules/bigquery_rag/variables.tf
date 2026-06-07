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

variable "reader_members" {
  type        = list(string)
  description = "Members that query the KB (agent SA): granted dataViewer + connectionUser."
  default     = []
}

variable "labels" {
  type    = map(string)
  default = {}
}
