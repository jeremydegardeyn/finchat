# The full, locations-qualified resource names.
#
# Constructed rather than read from `.name` deliberately: both consumers (the Dataflow
# pipeline and Model Armor's advanced_config) reject a name that is not in the
# `projects/P/locations/L/<kind>/<id>` form, and a provider that returned a bare or
# global-shaped name would fail at call time rather than at plan time. Interpolating
# `template_id` off the resource keeps the dependency edge intact.

output "inspect_template" {
  description = "Full regional resource name of the inspect template."
  value       = "projects/${var.project_id}/locations/${var.region}/inspectTemplates/${google_data_loss_prevention_inspect_template.pii.template_id}"
}

output "deidentify_template" {
  description = "Full regional resource name of the de-identify template."
  value       = "projects/${var.project_id}/locations/${var.region}/deidentifyTemplates/${google_data_loss_prevention_deidentify_template.mask.template_id}"
}

output "location" {
  description = "Template location. The DLP request parent must match it, or the call 404s."
  value       = var.region
}
