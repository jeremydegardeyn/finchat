output "aspect_type_ids" {
  description = "Map of aspect type -> resource name."
  value       = { for k, t in google_dataplex_aspect_type.types : k => t.name }
}
output "domain_entry_groups" {
  description = "Business-domain entry groups."
  value       = { for k, g in google_dataplex_entry_group.domains : k => g.name }
}
output "profile_scans" {
  description = "Per-product data-profile scans (Insights)."
  value       = { for k, s in google_dataplex_datascan.product_profile : k => s.name }
}
output "dq_scans" {
  value = {
    quality = google_dataplex_datascan.silver_txn_quality.name
  }
}
