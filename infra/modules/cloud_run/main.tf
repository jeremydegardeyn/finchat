###############################################################################
# Cloud Run module — generic scale-to-zero service (ADR-0007)
###############################################################################

resource "google_cloud_run_v2_service" "this" {
  project  = var.project_id
  location = var.region
  name     = var.service_name
  labels   = var.labels

  ingress = "INGRESS_TRAFFIC_ALL" # auth enforced via IAM/API Gateway, not network

  # Stated rather than inherited from the provider default, so an environment that
  # genuinely should be torn down can say so explicitly.
  deletion_protection = var.deletion_protection

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
        startup_cpu_boost = var.cpu_boost
        # Allocate CPU only while a request is in flight. This is the basis of the
        # near-zero idle cost premise (ADR-0002) and was previously left unset, so
        # Terraform cleared whatever the live service had. State it explicitly.
        cpu_idle = var.cpu_idle
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
      # SERVICE-level scaling, which is not the `template.scaling` block above. Nothing
      # here declares it, but the API materialises it with zeros, so Terraform proposed
      # removing a block the API will not remove — every plan showed all four services
      # "will be updated in-place" and every apply completed the update in 0s. A plan
      # that never reaches zero changes is a plan nobody reads, which is the real cost.
      #
      # Ignored rather than declared: `manual_instance_count` only means anything with
      # manual scaling, which these scale-to-zero services do not use, and writing 0 to
      # match the API would be asserting a value we do not actually manage.
      scaling,
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
