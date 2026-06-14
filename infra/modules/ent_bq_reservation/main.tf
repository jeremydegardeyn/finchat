# BigQuery Editions: an Enterprise reservation with autoscale (predictable slot
# capacity instead of on-demand per-byte), plus BI Engine for sub-second BI.
# Reference overlay — not applied.

variable "project_id" { type = string }
variable "location" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "edition" {
  type    = string
  default = "ENTERPRISE"
}
variable "baseline_slots" {
  type    = number
  default = 100
}
variable "max_slots" {
  type    = number
  default = 500
}

resource "google_bigquery_reservation" "primary" {
  project           = var.project_id
  name              = "${var.name_prefix}-${var.env}-reservation"
  location          = var.location
  edition           = var.edition
  slot_capacity     = var.baseline_slots
  ignore_idle_slots = false
  autoscale {
    max_slots = var.max_slots
  }
}

resource "google_bigquery_reservation_assignment" "query" {
  project     = var.project_id
  location    = var.location
  reservation = google_bigquery_reservation.primary.id
  assignee    = "projects/${var.project_id}"
  job_type    = "QUERY"
}

# In-memory acceleration for the DaaS/BI serving queries.
resource "google_bigquery_bi_reservation" "bi" {
  project  = var.project_id
  location = var.location
  size     = 1073741824 # 1 GiB
}

output "reservation_id" { value = google_bigquery_reservation.primary.id }
