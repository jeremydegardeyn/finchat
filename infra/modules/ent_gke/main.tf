# GKE Autopilot — the enterprise compute substrate for the services that run as
# Cloud Run in the sandbox. Private nodes on the VPC, Workload Identity, container-
# native networking via the subnet's secondary ranges. Reference overlay — not
# applied. Sample workload manifests live in ./manifests (also not applied).

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "network_id" { type = string }
variable "subnet_id" { type = string }
variable "pods_range_name" { type = string }
variable "services_range_name" { type = string }

resource "google_container_cluster" "autopilot" {
  provider         = google-beta
  project          = var.project_id
  name             = "${var.name_prefix}-${var.env}-gke"
  location         = var.region
  enable_autopilot = true

  network    = var.network_id
  subnetwork = var.subnet_id

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  release_channel {
    channel = "REGULAR"
  }

  deletion_protection = false
  # Autopilot enables Workload Identity (workload pool <project>.svc.id.goog) by default.
}

# GСP service account a workload assumes via Workload Identity (e.g. the txn-api).
resource "google_service_account" "txn_api" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-${var.env}-txn-api"
  display_name = "txn-api (GKE Workload Identity)"
}

# Bind the Kubernetes SA (namespace `serving`, KSA `txn-api`) to the GCP SA.
resource "google_service_account_iam_member" "txn_api_wi" {
  service_account_id = google_service_account.txn_api.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[serving/txn-api]"
}

output "cluster_name" { value = google_container_cluster.autopilot.name }
output "workload_sa_email" { value = google_service_account.txn_api.email }
