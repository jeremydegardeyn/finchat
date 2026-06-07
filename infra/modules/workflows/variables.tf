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

variable "workflow_source" {
  type        = string
  description = "Cloud Workflows YAML/JSON source for the loan approval orchestration."
}

variable "service_account" {
  type        = string
  description = "SA the workflow executes as (run.invoker, bigquery)."
}

variable "env_vars" {
  type        = map(string)
  description = "Runtime env vars for the workflow (read via sys.get_env), e.g. LOAN_API_URL/TXN_API_URL."
  default     = {}
}

variable "labels" {
  type    = map(string)
  default = {}
}
