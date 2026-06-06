output "gateway_url" {
  description = "Managed gateway default hostname."
  value       = google_api_gateway_gateway.gateway.default_hostname
}
output "api_id" {
  value = google_api_gateway_api.api.api_id
}
