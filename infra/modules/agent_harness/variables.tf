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
  description = "Initial image for the Cloud Run shell. CI/CD overwrites it (ignore_changes), so the placeholder is only what runs until the first deploy."
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL tier — the durable 'autosave'. Smallest shared-core for the sandbox."
  default     = "db-f1-micro"
}

variable "run_sa_email" {
  type        = string
  description = "Service account the steward Cloud Run service runs as (reads the DB-URL secret + connects via the Cloud SQL connector)."
}

variable "scheduler_sa_email" {
  type        = string
  description = "Service account Cloud Scheduler uses to invoke the steward (OIDC) and to start/stop Cloud SQL."
}

variable "invoker_members" {
  type        = list(string)
  description = "Members granted run.invoker on the steward (e.g. the BFF SA + the scheduler SA)."
  default     = []
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
# before the run and STOP it after the window — paying compute ~1 of 24h (stopped
# ≈ storage/backups only, ~$2-4/mo). A stopped DB can't receive a wake, so CI sets
# HUMAN_WAIT_SECONDS small enough that escalations AUTO-DEFER to the next window.
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

variable "labels" {
  type    = map(string)
  default = {}
}
