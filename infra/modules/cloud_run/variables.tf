variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "service_name" { type = string }

variable "image" {
  type        = string
  description = "Container image (defaults to a placeholder hello image until CI builds the real one)."
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "service_account" {
  type        = string
  description = "Runtime service account email (least privilege)."
}

variable "env_vars" {
  type    = map(string)
  default = {}
}

# Scale-to-zero by default (near-zero cost). Set min_instances>0 for warm enterprise SLOs.
variable "min_instances" {
  type    = number
  default = 0
}
variable "max_instances" {
  type    = number
  default = 4
}

variable "allow_unauthenticated" {
  type        = bool
  description = "Public access. Keep false; front with API Gateway / IAM."
  default     = false
}

variable "cpu" {
  type    = string
  default = "1"
}
variable "memory" {
  type    = string
  default = "512Mi"
}

variable "labels" {
  type    = map(string)
  default = {}
}
