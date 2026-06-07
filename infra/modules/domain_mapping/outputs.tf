output "dns_records" {
  description = "DNS records to create at the domain registrar (datadinosaur.com)."
  value       = google_cloud_run_domain_mapping.this.status[0].resource_records
}

output "mapped_domain" {
  value = google_cloud_run_domain_mapping.this.name
}
