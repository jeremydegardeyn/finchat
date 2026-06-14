# VPC Service Controls perimeter around the host project's data services, so data
# can't be read/exfiltrated to projects outside the perimeter even with valid IAM.
# Reference overlay — not applied. Requires an Access Context Manager policy.

variable "project_number" { type = string }
variable "access_policy_id" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }

resource "google_access_context_manager_service_perimeter" "perimeter" {
  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/${var.name_prefix}_${var.env}"
  title  = "${var.name_prefix}-${var.env} perimeter"

  status {
    resources = ["projects/${var.project_number}"]
    restricted_services = [
      "bigquery.googleapis.com",
      "storage.googleapis.com",
      "spanner.googleapis.com",
      "bigtable.googleapis.com",
      "pubsub.googleapis.com",
      "dataflow.googleapis.com",
      "aiplatform.googleapis.com",
      "dlp.googleapis.com",
      "cloudkms.googleapis.com",
    ]
    vpc_accessible_services {
      enable_restriction = true
      allowed_services   = ["RESTRICTED-SERVICES"]
    }
  }
}

output "perimeter_name" { value = google_access_context_manager_service_perimeter.perimeter.name }
