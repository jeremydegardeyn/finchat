# Apigee X — full API management (the sandbox uses API Gateway). Org + runtime
# instance + environment + env group, peered to the VPC. API proxy bundles are
# deployed separately (apigeecli / CI), referenced in ./proxies. Reference overlay
# — not applied. (Apigee X carries significant standing cost.)

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "domain" { type = string }
variable "network_id" { type = string }

resource "google_apigee_organization" "org" {
  project_id         = var.project_id
  analytics_region   = var.region
  runtime_type       = "CLOUD"
  authorized_network = var.network_id
}

resource "google_apigee_instance" "runtime" {
  org_id   = google_apigee_organization.org.id
  name     = "${var.name_prefix}-${var.env}-apigee"
  location = var.region
}

resource "google_apigee_environment" "prod" {
  org_id = google_apigee_organization.org.id
  name   = "prod"
}

resource "google_apigee_envgroup" "default" {
  org_id    = google_apigee_organization.org.id
  name      = "${var.name_prefix}-${var.env}-eg"
  hostnames = ["api.${var.domain}"]
}

resource "google_apigee_instance_attachment" "prod" {
  instance_id = google_apigee_instance.runtime.id
  environment = google_apigee_environment.prod.name
}

resource "google_apigee_envgroup_attachment" "prod" {
  envgroup_id = google_apigee_envgroup.default.id
  environment = google_apigee_environment.prod.name
}

output "org_id" { value = google_apigee_organization.org.id }
