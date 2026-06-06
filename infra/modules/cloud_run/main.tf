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
    # CI/CD pushes new images; ignore image drift so terraform doesn't fight deploys.
    ignore_changes = [template[0].containers[0].image, client, client_version]
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
