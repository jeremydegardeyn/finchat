variable "project_id" { type = string }
variable "region" { type = string }
variable "domain" {
  type        = string
  description = "Custom domain to map to the Cloud Run service, e.g. finchat.datadinosaur.com."
}
variable "service_name" {
  type        = string
  description = "Target Cloud Run service name (the UI)."
}
