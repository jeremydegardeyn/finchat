output "kb_dataset" {
  value = google_bigquery_dataset.kb.dataset_id
}
output "connection_id" {
  description = "Full connection id for the remote embedding model DDL (project.location.connection)."
  value       = "${var.project_id}.${var.region}.${google_bigquery_connection.kb.connection_id}"
}
output "embedding_model" {
  value = "${var.project_id}.${google_bigquery_dataset.kb.dataset_id}.embedding_model"
}
