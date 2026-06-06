output "inspect_template" {
  value = google_data_loss_prevention_inspect_template.pii.name
}
output "deidentify_template" {
  value = google_data_loss_prevention_deidentify_template.mask.name
}
