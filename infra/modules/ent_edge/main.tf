# Global external HTTPS load balancer fronting the services, with Cloud CDN for
# static assets and Cloud Armor (WAF + rate limiting) on the backend. Reference
# overlay — not applied. Backend is a serverless NEG here for brevity; GKE would
# use container-native NEGs from the workload Services.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "domain" { type = string }
variable "cloud_run_service" {
  type    = string
  default = "finchat-enterprise-ui"
}

resource "google_compute_security_policy" "armor" {
  project = var.project_id
  name    = "${var.name_prefix}-${var.env}-armor"

  # Per-IP rate limiting.
  rule {
    action   = "rate_based_ban"
    priority = 1000
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 600
        interval_sec = 60
      }
    }
  }

  # Preconfigured OWASP WAF (SQLi).
  rule {
    action   = "deny(403)"
    priority = 1100
    match {
      expr { expression = "evaluatePreconfiguredExpr('sqli-v33-stable')" }
    }
  }

  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
  }
}

resource "google_compute_region_network_endpoint_group" "neg" {
  project               = var.project_id
  name                  = "${var.name_prefix}-${var.env}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = var.cloud_run_service
  }
}

resource "google_compute_backend_service" "backend" {
  project               = var.project_id
  name                  = "${var.name_prefix}-${var.env}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  enable_cdn            = true
  security_policy       = google_compute_security_policy.armor.id
  cdn_policy {
    cache_mode  = "CACHE_ALL_STATIC"
    default_ttl = 3600
  }
  backend {
    group = google_compute_region_network_endpoint_group.neg.id
  }
}

resource "google_compute_url_map" "map" {
  project         = var.project_id
  name            = "${var.name_prefix}-${var.env}-urlmap"
  default_service = google_compute_backend_service.backend.id
}

resource "google_compute_managed_ssl_certificate" "cert" {
  project = var.project_id
  name    = "${var.name_prefix}-${var.env}-cert"
  managed {
    domains = ["app.${var.domain}"]
  }
}

resource "google_compute_target_https_proxy" "proxy" {
  project          = var.project_id
  name             = "${var.name_prefix}-${var.env}-https-proxy"
  url_map          = google_compute_url_map.map.id
  ssl_certificates = [google_compute_managed_ssl_certificate.cert.id]
}

resource "google_compute_global_forwarding_rule" "fr" {
  project               = var.project_id
  name                  = "${var.name_prefix}-${var.env}-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  target                = google_compute_target_https_proxy.proxy.id
}

output "backend_service_id" { value = google_compute_backend_service.backend.id }
