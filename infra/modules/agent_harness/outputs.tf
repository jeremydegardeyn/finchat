output "service_uri" {
  description = "Steward Cloud Run URL (BFF proxies the approver-gated /runs endpoints here)."
  value       = google_cloud_run_v2_service.steward.uri
}

output "sql_instance" {
  description = "Cloud SQL instance name (the durable autosave)."
  value       = google_sql_database_instance.steward.name
}

output "sql_connection_name" {
  value = google_sql_database_instance.steward.connection_name
}
