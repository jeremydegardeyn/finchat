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

variable "enable_floor_setting" {
  type        = bool
  description = "Also create a project-level floor setting (org-wide minimum screening). Needs elevated perms."
  default     = false
}

variable "confidence_level" {
  type        = string
  description = "Detection sensitivity for RAI/PI filters."
  default     = "MEDIUM_AND_ABOVE"
}
