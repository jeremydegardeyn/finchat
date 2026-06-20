###############################################################################
# Agent Harness module — durable long-running agent serving tier (ADR-0021).
# DEFAULT OFF. Deploy tier = DBOS on Cloud Run (scale-to-zero) backed by a small
# Cloud SQL Postgres (the durable "autosave"). Turnkey:
#   - the DB password is generated (random_password) and stored in Secret Manager
#     — never in tfvars/code;
#   - Cloud Run connects via the Cloud SQL CONNECTOR (unix socket), so no public
#     IP appears in the connection string and Cloud Run egress IPs need no
#     allow-listing;
#   - TF owns the SQL, the secret, and the Cloud Run *shell*; CI/CD owns the image
#     + runtime env (ignore_changes), exactly like the other services.
# Cloud SQL has no scale-to-zero, so the sandbox keeps enable_agent_harness=false
# and develops against a local Postgres. Enterprise 1:1 = Temporal (ADR-0021).
###############################################################################

# --- Durable state store: the agent's autosave -------------------------------
resource "google_sql_database_instance" "steward" {
  project             = var.project_id
  name                = "${var.name_prefix}-${var.env}-steward"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = false # sandbox: allow teardown; enterprise: true

  settings {
    tier    = var.db_tier
    edition = "ENTERPRISE" # shared-core tiers (db-f1-micro/db-g1-small) require ENTERPRISE, not ENTERPRISE_PLUS

    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled = true # reached only via the Cloud SQL connector; no authorized networks
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

resource "random_password" "db" {
  length  = 32
  special = false # URL-safe — embedded in the connection string below
}

resource "google_sql_user" "steward" {
  project  = var.project_id
  name     = "steward"
  instance = google_sql_database_instance.steward.name
  password = random_password.db.result
}

# --- DB connection string in Secret Manager (connector socket, no public IP) --
resource "google_secret_manager_secret" "db_url" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-${var.env}-steward-db-url"
  labels    = var.labels
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_url" {
  secret      = google_secret_manager_secret.db_url.id
  secret_data = "postgresql://steward:${random_password.db.result}@/finchat_steward?host=/cloudsql/${google_sql_database_instance.steward.connection_name}"
}

# The steward reads the DB-URL secret and connects through the Cloud SQL connector.
resource "google_secret_manager_secret_iam_member" "run_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.db_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.run_sa_email}"
}

resource "google_project_iam_member" "run_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${var.run_sa_email}"
}

# --- The durable agent: Cloud Run shell (CI/CD owns image + env) --------------
resource "google_cloud_run_v2_service" "steward" {
  project             = var.project_id
  name                = "${var.name_prefix}-${var.env}-steward"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL" # auth via IAM, not network

  template {
    service_account = var.run_sa_email

    scaling {
      min_instance_count = 0 # scale to zero
      max_instance_count = 2
    }

    # Cloud SQL connector — exposes the instance as a unix socket at /cloudsql/<conn>.
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.steward.connection_name]
      }
    }

    containers {
      image = var.image # placeholder until CI deploys the real steward image
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }

  labels = var.labels

  lifecycle {
    # CI/CD owns runtime deploys (image + env incl. the DBOS_DATABASE_URL secret +
    # the Cloud SQL connector). TF provisions the shell but never fights it.
    ignore_changes = [
      template[0].containers[0].image,
      template[0].containers[0].env,
      template[0].containers[0].volume_mounts,
      template[0].volumes,
      client,
      client_version,
    ]
  }
}

# Invokers: the BFF SA (proxies /api/steward/*) and the scheduler SA (nightly run).
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invoker_members)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.steward.name
  role     = "roles/run.invoker"
  member   = each.value
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

# --- Option B: nightly stop/start of the Cloud SQL autosave -------------------
# Patch the instance's activationPolicy via the Cloud SQL Admin API (ALWAYS=start,
# NEVER=stop). The scheduler SA needs to modify the instance.
resource "google_project_iam_member" "scheduler_sql_editor" {
  count   = var.enable_scheduled_stop ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.editor"
  member  = "serviceAccount:${var.scheduler_sa_email}"
}

resource "google_cloud_scheduler_job" "sql_start" {
  count     = var.enable_scheduled_stop ? 1 : 0
  project   = var.project_id
  region    = var.region
  name      = "${var.name_prefix}-${var.env}-steward-sql-start"
  schedule  = var.start_cron
  time_zone = "America/New_York"

  http_target {
    uri         = "https://sqladmin.googleapis.com/v1/projects/${var.project_id}/instances/${google_sql_database_instance.steward.name}"
    http_method = "PATCH"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(jsonencode({ settings = { activationPolicy = "ALWAYS" } }))
    oauth_token { service_account_email = var.scheduler_sa_email }
  }
}

resource "google_cloud_scheduler_job" "sql_stop" {
  count     = var.enable_scheduled_stop ? 1 : 0
  project   = var.project_id
  region    = var.region
  name      = "${var.name_prefix}-${var.env}-steward-sql-stop"
  schedule  = var.stop_cron
  time_zone = "America/New_York"

  http_target {
    uri         = "https://sqladmin.googleapis.com/v1/projects/${var.project_id}/instances/${google_sql_database_instance.steward.name}"
    http_method = "PATCH"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode(jsonencode({ settings = { activationPolicy = "NEVER" } }))
    oauth_token { service_account_email = var.scheduler_sa_email }
  }
}
