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

variable "image" {
  type        = string
  description = "Steward harness container image (products/steward/harness)."
  default     = "us-docker.pkg.dev/cloudrun/container/hello" # placeholder until CI builds it
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL tier — the durable 'autosave'. Smallest shared-core for the sandbox."
  default     = "db-f1-micro"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Password for the steward Postgres user (provide via tfvars/secret, never commit)."
  default     = "change-me-in-tfvars"
}

variable "run_sa_email" {
  type        = string
  description = "Service account the steward Cloud Run service runs as."
}

variable "scheduler_sa_email" {
  type        = string
  description = "Service account Cloud Scheduler uses to invoke the steward (OIDC)."
}

variable "schedule_cron" {
  type        = string
  description = "Nightly reconciliation window (Cloud Scheduler cron)."
  default     = "0 3 * * *"
}

variable "schedule_goal" {
  type    = string
  default = "Reconcile yesterday's ledger and flag anomalies"
}

variable "labels" {
  type    = map(string)
  default = {}
}
