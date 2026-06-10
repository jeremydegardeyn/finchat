###############################################################################
# Cloud Run module — generic scale-to-zero service (ADR-0007)
###############################################################################

resource "google_cloud_run_v2_service" "this" {
  project  = var.project_id
  location = var.region
  name     = var.service_name
  labels   = var.labels

  ingress = "INGRESS_TRAFFIC_ALL" # auth enforced via IAM/API Gateway, not network

  template {
    service_account = var.service_account
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = var.image
      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }
      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  lifecycle {
    # CI/CD owns runtime deploys: it pushes new images AND sets the full env var set
    # via `gcloud run deploy --set-env-vars` (e.g. the UI BFF's backend URLs, datasets,
    # CA/Vertex locations). Ignore image + env drift so `terraform apply` provisions the
    # service skeleton but never fights/clobbers the CI-deployed runtime config.
    ignore_changes = [
      template[0].containers[0].image,
      template[0].containers[0].env,
      client,
      client_version,
    ]
  }
}

# Default: private. Invokers granted explicitly (API Gateway SA, other services).
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Explicit invokers (e.g., API Gateway SA) for private services.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invokers)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}
