output "bronze_dataset" { value = google_bigquery_dataset.bronze.dataset_id }
output "silver_dataset" { value = google_bigquery_dataset.silver.dataset_id }
output "gold_dataset" { value = google_bigquery_dataset.gold.dataset_id }

output "taxonomy_id" {
  description = "Data Catalog taxonomy resource id."
  value       = google_data_catalog_taxonomy.classification.id
}

output "policy_tag_ids" {
  description = "Map of classification -> policy tag id."
  value       = { for k, t in google_data_catalog_policy_tag.tags : k => t.id }
}

output "bronze_transaction_event_table" {
  description = "Fully-qualified Bronze landing table for the Pub/Sub BQ subscription (project.dataset.table)."
  value       = "${var.project_id}.${google_bigquery_dataset.bronze.dataset_id}.${google_bigquery_table.bronze_transaction_event.table_id}"
}

output "silver_transaction_table" {
  description = "Silver transaction table in project:dataset.table form (for Dataflow)."
  value       = "${var.project_id}:${google_bigquery_dataset.silver.dataset_id}.${google_bigquery_table.silver_transaction.table_id}"
}

output "gold_account_summary" {
  value = "${var.project_id}.${google_bigquery_dataset.gold.dataset_id}.${google_bigquery_table.gold_account_summary.table_id}"
}
