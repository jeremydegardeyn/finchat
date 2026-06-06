###############################################################################
# API Gateway module — DaaS front door (ADR-0006, substitutes Apigee X)
# OpenAPI-driven; the SAME spec imports into Apigee for the enterprise path.
###############################################################################

resource "google_api_gateway_api" "api" {
  provider = google-beta
  project  = var.project_id
  api_id   = "${var.name_prefix}-${var.env}-daas"
  labels   = var.labels
}

resource "google_api_gateway_api_config" "config" {
  provider      = google-beta
  project       = var.project_id
  api           = google_api_gateway_api.api.api_id
  api_config_id = "${var.name_prefix}-${var.env}-cfg"

  openapi_documents {
    document {
      path     = "openapi.yaml"
      contents = var.openapi_spec
    }
  }

  gateway_config {
    backend_config {
      google_service_account = var.gateway_service_account
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_api_gateway_gateway" "gateway" {
  provider   = google-beta
  project    = var.project_id
  region     = var.region
  api_config = google_api_gateway_api_config.config.id
  gateway_id = "${var.name_prefix}-${var.env}-gw"
  labels     = var.labels
}
