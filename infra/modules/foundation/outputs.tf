output "service_account_emails" {
  description = "Map of workload -> service account email."
  value       = { for k, sa in google_service_account.sa : k => sa.email }
}

output "artifact_registry_repo" {
  description = "Artifact Registry repo path for image pushes."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "dataflow_bucket" {
  description = "GCS bucket for Dataflow temp/staging + Flex Templates."
  value       = google_storage_bucket.dataflow.name
}

output "bronze_raw_bucket" {
  description = "GCS bucket backing BigLake Bronze raw landing."
  value       = google_storage_bucket.bronze_raw.name
}
