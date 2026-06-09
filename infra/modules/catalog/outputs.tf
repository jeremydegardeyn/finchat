output "aspect_type_ids" {
  description = "Map of aspect type -> resource name."
  value       = { for k, t in google_dataplex_aspect_type.types : k => t.name }
}
output "domain_entry_groups" {
  description = "Business-domain entry groups."
  value       = { for k, g in google_dataplex_entry_group.domains : k => g.name }
}
output "dq_scans" {
  value = {
    profile = google_dataplex_datascan.silver_txn_profile.name
    quality = google_dataplex_datascan.silver_txn_quality.name
  }
}
