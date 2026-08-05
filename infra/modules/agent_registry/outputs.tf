output "agent_service_account_emails" {
  description = "agent_id -> distinct service account email."
  value       = { for id, sa in google_service_account.agent : id => sa.email }
}

output "agent_service_account_names" {
  description = "agent_id -> fully-qualified SA resource name (for impersonation grants)."
  value       = { for id, sa in google_service_account.agent : id => sa.name }
}

output "dataset_id" {
  description = "Platform control-plane dataset holding the registry and action log."
  value       = google_bigquery_dataset.platform.dataset_id
}

output "registry_table" {
  value = "${var.project_id}.${google_bigquery_dataset.platform.dataset_id}.${google_bigquery_table.agent_registry.table_id}"
}

output "action_log_table" {
  value = "${var.project_id}.${google_bigquery_dataset.platform.dataset_id}.${google_bigquery_table.agent_action_log.table_id}"
}
