###############################################################################
# Foundation module
# Enables required APIs, creates per-service least-privilege service accounts,
# Artifact Registry, platform buckets, and an optional billing budget.
###############################################################################

locals {
  prefix = "${var.name_prefix}-${var.env}"

  # One service account per workload (least privilege; never reuse the default SA).
  service_accounts = {
    pipeline = { display = "Dataflow / ingestion pipeline", roles = [
      "roles/dataflow.worker",
      "roles/pubsub.subscriber",
      "roles/bigquery.dataEditor",
      "roles/bigquery.jobUser",
      "roles/storage.objectAdmin",
      "roles/dlp.user",
      "roles/artifactregistry.reader", # Dataflow workers pull the Flex Template image
    ] }
    txn_api = { display = "Transactions DaaS API + UI BFF (Cloud Run)", roles = [
      "roles/bigquery.dataViewer",
      "roles/bigquery.jobUser",
      "roles/modelarmor.user",                            # UI BFF screens agent I/O
      "roles/dataplex.catalogViewer",                     # Analyst: catalog discovery search
      "roles/geminidataanalytics.dataAgentStatelessUser", # Analyst: Conversational Analytics chat
      "roles/aiplatform.user",                            # Analyst: Gemini intent router (KB vs analytics)
    ] }
    loan_api = { display = "Loan API (Cloud Run)", roles = [
      "roles/bigquery.dataEditor",
      "roles/bigquery.jobUser",
      "roles/workflows.invoker",
    ] }
    agent = { display = "Conversational + loan agents", roles = [
      "roles/aiplatform.user",
      "roles/bigquery.dataViewer",
      "roles/bigquery.jobUser",
      "roles/run.invoker",
      "roles/dataplex.catalogViewer", # discover_data_product: search the catalog
    ] }
    workflow = { display = "Loan Cloud Workflows orchestrator", roles = [
      "roles/run.invoker",
      "roles/bigquery.dataEditor",
      "roles/bigquery.jobUser",
      "roles/workflows.invoker",
    ] }
    cicd = { display = "CI/CD deployer (GitHub Actions via WIF)", roles = [
      "roles/run.developer",
      "roles/artifactregistry.writer",
      "roles/cloudbuild.builds.editor",
      "roles/iam.serviceAccountUser",
      "roles/dataflow.developer",  # build/launch the Flex Template
      "roles/storage.objectAdmin", # write the Flex Template spec to the dataflow bucket
      "roles/bigquery.jobUser",    # live-eval scorer: query conversation_log
      "roles/aiplatform.user",     # live-eval scorer: Vertex Gen AI Evaluation
    ] }
  }

  required_apis = [
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "bigquery.googleapis.com",
    "bigquerystorage.googleapis.com",
    "bigqueryconnection.googleapis.com",
    "biglake.googleapis.com",
    "storage.googleapis.com",
    "pubsub.googleapis.com",
    "dataflow.googleapis.com",
    "run.googleapis.com",
    "apigateway.googleapis.com",
    "servicecontrol.googleapis.com",
    "servicemanagement.googleapis.com",
    "workflows.googleapis.com",
    "workflowexecutions.googleapis.com",
    "cloudscheduler.googleapis.com",
    "aiplatform.googleapis.com",
    "modelarmor.googleapis.com",
    "dlp.googleapis.com",
    "datacatalog.googleapis.com",
    "dataplex.googleapis.com",
    "datalineage.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "eventarc.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
}

# --- Enable APIs -------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each                   = toset(local.required_apis)
  project                    = var.project_id
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- Service accounts --------------------------------------------------------
resource "google_service_account" "sa" {
  for_each = local.service_accounts
  project  = var.project_id
  # account_id allows only [a-z0-9-]; map keys use underscores as logical names.
  account_id   = "${local.prefix}-${replace(each.key, "_", "-")}"
  display_name = "${each.value.display} (${var.env})"
  depends_on   = [google_project_service.apis]
}

# Flatten (sa, role) pairs for least-privilege project bindings.
locals {
  sa_role_pairs = merge([
    for sa_key, sa in local.service_accounts : {
      for role in sa.roles : "${sa_key}|${role}" => {
        sa_key = sa_key
        role   = role
      }
    }
  ]...)
}

resource "google_project_iam_member" "sa_roles" {
  for_each = local.sa_role_pairs
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.sa[each.value.sa_key].email}"
}

# --- Artifact Registry (container images) ------------------------------------
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "${local.prefix}-images"
  format        = "DOCKER"
  description   = "FinChat service container images (${var.env})"
  labels        = var.labels
  depends_on    = [google_project_service.apis]
}

# --- Platform buckets --------------------------------------------------------
# Dataflow temp/staging + Flex Template specs (autoclass to control cost).
resource "google_storage_bucket" "dataflow" {
  project                     = var.project_id
  name                        = "${local.prefix}-dataflow"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  labels                      = var.labels

  lifecycle_rule {
    condition { age = 7 }
    action { type = "Delete" }
  }
}

# Bronze raw landing for BigLake managed/external tables (long retention, cold tiering).
resource "google_storage_bucket" "bronze_raw" {
  project                     = var.project_id
  name                        = "${local.prefix}-bronze-raw"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  labels                      = var.labels

  lifecycle_rule {
    condition { age = 90 }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
  lifecycle_rule {
    condition { age = 400 }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }
}

# --- Billing budget (optional; near-zero-cost guardrail) ----------------------
resource "google_billing_budget" "budget" {
  count           = var.enable_budget ? 1 : 0
  billing_account = var.billing_account
  display_name    = "${local.prefix}-budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount_usd)
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }
}
