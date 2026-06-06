output "service_name" {
  value = google_cloud_run_v2_service.this.name
}
output "uri" {
  description = "Default HTTPS URL of the service."
  value       = google_cloud_run_v2_service.this.uri
}
