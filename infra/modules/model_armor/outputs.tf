output "template_id" {
  description = "Short template id (for the :sanitize* REST calls)."
  value       = google_model_armor_template.this.template_id
}

output "template_name" {
  description = "Full resource name of the Model Armor template."
  value       = "projects/${var.project_id}/locations/${var.region}/templates/${google_model_armor_template.this.template_id}"
}
