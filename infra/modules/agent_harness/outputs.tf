output "service_uri" {
  description = "Steward Cloud Run URL (BFF proxies the approver-gated /runs endpoints here)."
  value       = google_cloud_run_v2_service.steward.uri
}

output "service_name" {
  value = google_cloud_run_v2_service.steward.name
}

output "sql_instance" {
  description = "Cloud SQL instance name (the durable autosave)."
  value       = google_sql_database_instance.steward.name
}

output "sql_connection_name" {
  description = "Cloud SQL connection name (PROJECT:REGION:INSTANCE) — used by the Cloud Run connector."
  value       = google_sql_database_instance.steward.connection_name
}

output "db_url_secret" {
  description = "Secret Manager secret id holding the DBOS_DATABASE_URL (connector socket)."
  value       = google_secret_manager_secret.db_url.secret_id
}
