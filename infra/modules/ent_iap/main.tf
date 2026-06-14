# Identity-Aware Proxy on the staff surface: zero-trust auth at the LB before any
# request reaches a service. Brand + OAuth client + access binding to the staff
# group. Reference overlay — not applied.

variable "project_id" { type = string }
variable "support_email" { type = string }
variable "staff_group" {
  type        = string
  description = "Group granted access through IAP, e.g. group:staff@datadinosaur.com"
}
variable "backend_service_id" { type = string }

resource "google_iap_brand" "brand" {
  project           = var.project_id
  support_email     = var.support_email
  application_title = "FinChat (staff)"
}

resource "google_iap_client" "client" {
  display_name = "FinChat IAP"
  brand        = google_iap_brand.brand.name
}

# Only the staff group may pass IAP to the backend.
resource "google_iap_web_backend_service_iam_member" "staff" {
  project             = var.project_id
  web_backend_service = var.backend_service_id
  role                = "roles/iap.httpsResourceAccessor"
  member              = var.staff_group
}
