###############################################################################
# Agent Harness module — durable long-running agent serving tier (ADR-0021).
# DEFAULT OFF. Deploy tier = DBOS on Cloud Run (scale-to-zero) backed by a small
# Cloud SQL Postgres (the durable "autosave"). Cloud SQL has no scale-to-zero, so
# the sandbox keeps enable_agent_harness=false and develops against a local Postgres.
# Enterprise 1:1 = Temporal (documented, not deployed) — see ADR-0021.
###############################################################################

# --- Durable state store: the agent's autosave -------------------------------
resource "google_sql_database_instance" "steward" {
  project             = var.project_id
  name                = "${var.name_prefix}-${var.env}-steward"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = false # sandbox: allow teardown; enterprise: true

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled = true # sandbox simplicity; enterprise: private IP + connector
    }

    backup_configuration {
      enabled = true
    }

    user_labels = var.labels
  }
}

resource "google_sql_database" "steward" {
  project  = var.project_id
  name     = "finchat_steward"
  instance = google_sql_database_instance.steward.name
}

resource "google_sql_user" "steward" {
  project  = var.project_id
  name     = "steward"
  instance = google_sql_database_instance.steward.name
  password = var.db_password
}

# --- The durable agent: Cloud Run, scale-to-zero -----------------------------
# While DBOS.sleep()/recv() is parked the instance can be evicted (zero cost);
# the workflow recovers from Postgres on the next request/wake.
resource "google_cloud_run_v2_service" "steward" {
  project             = var.project_id
  name                = "${var.name_prefix}-${var.env}-steward"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = var.run_sa_email

    scaling {
      min_instance_count = 0 # scale to zero
      max_instance_count = 2
    }

    containers {
      image = var.image

      env {
        name  = "DBOS_DATABASE_URL"
        value = "postgresql://steward:${var.db_password}@${google_sql_database_instance.steward.public_ip_address}:5432/finchat_steward"
      }
      env {
        name  = "EVAL_THRESHOLD"
        value = "0.6"
      }
    }
  }

  labels = var.labels
}

# Cloud Scheduler invokes the steward (OIDC); requires run.invoker.
resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.steward.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.scheduler_sa_email}"
}

# --- Wake trigger: nightly schedule (push, not polling) ----------------------
resource "google_cloud_scheduler_job" "nightly" {
  project   = var.project_id
  region    = var.region
  name      = "${var.name_prefix}-${var.env}-steward-nightly"
  schedule  = var.schedule_cron
  time_zone = "America/New_York"

  http_target {
    uri         = "${google_cloud_run_v2_service.steward.uri}/runs"
    http_method = "POST"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(jsonencode({ goal = var.schedule_goal }))

    oidc_token {
      service_account_email = var.scheduler_sa_email
      audience              = google_cloud_run_v2_service.steward.uri
    }
  }
}
