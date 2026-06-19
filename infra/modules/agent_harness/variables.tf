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

# --- Option B: scheduled stop/start of the Cloud SQL "autosave" ---------------
# Cloud SQL has no scale-to-zero. The steward is nightly, so we START the instance
# just before the run and STOP it after the window — paying compute only ~1 of 24h
# (stopped ≈ storage/backups only, ~$2-4/mo). A stopped instance cannot receive a
# wake, so in scheduled mode escalations must AUTO-DEFER to the next window
# (human_wait_seconds keeps the agent inside the daily window).
variable "enable_scheduled_stop" {
  type        = bool
  description = "Start Cloud SQL before the nightly run and stop it after (Option B). Set false for 24/7."
  default     = true
}

variable "start_cron" {
  type        = string
  description = "Start the Cloud SQL instance shortly before the run."
  default     = "50 2 * * *"
}

variable "stop_cron" {
  type        = string
  description = "Stop the Cloud SQL instance after the run window (leave room for same-window review)."
  default     = "0 5 * * *"
}

variable "human_wait_seconds" {
  type        = number
  description = "How long an escalation waits before auto-deferring. In scheduled-stop mode keep this inside the on-window (default 2h)."
  default     = 7200
}

variable "labels" {
  type    = map(string)
  default = {}
}
