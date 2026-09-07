###############################################################################
# Foundation module
# Enables required APIs, creates per-service least-privilege service accounts,
# Artifact Registry, platform buckets, and an optional billing budget.
###############################################################################

# Resolves the project NUMBER. Several Google APIs — the Budget API among them — accept
# a project id on write and return the number on read, so anything comparing the two
# needs the number to avoid a diff that can never converge.
data "google_project" "this" {
  project_id = var.project_id
}

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
      "roles/geminidataanalytics.dataAgentStatelessUser", # Analyst: Conversational Analytics chat (inline fallback)
      "roles/geminidataanalytics.dataAgentUser",          # Analyst: chat via the persistent Data Agent (ADR-0018)
      "roles/aiplatform.user",                            # Analyst: Gemini intent router (KB vs analytics)
      # One-time sign-in (ADR-0025): the BFF exchanges an auth code for an identity plus
      # an access token, and stores the refresh token so later sessions never prompt.
      "roles/datastore.user",               # refresh-token store (Firestore)
      "roles/secretmanager.secretAccessor", # OAuth client secret for the code exchange
    ] }
    # Process + experience layers (ADR-0030). Both hold NO project roles on purpose:
    # they reach nothing directly, only other Cloud Run services, and that is granted
    # per-target via `invokers` rather than project-wide. A process API that needed
    # bigquery.dataViewer would be a process API that had started querying.
    process = { display = "Process API — cross-domain composition (Cloud Run)", roles = [] }
    mobile  = { display = "Mobile Experience API (Cloud Run)", roles = [] }
    # The agent channel (ADR-0028/0031), deployed over streamable HTTP. Also empty, and
    # for a sharper reason than the two above: this identity is what a remote MCP client
    # is ultimately acting as, so every project role granted here is a role granted to
    # every caller of the MCP endpoint. It reaches data only through the same governed
    # APIs the web channel uses, each granted per-target below.
    mcp = { display = "MCP server — agent channel over HTTP (Cloud Run)", roles = [] }
    # The OAuth proxy (ADR-0020). It reads the Google client secret and nothing
    # else: it never touches data, never calls another FinChat service, and is the
    # only service here reachable from the public internet by design. Firestore is
    # for its own clients/codes/refresh records.
    # Firestore only. `roles/secretmanager.secretAccessor` was here and should not have
    # been: at project level it reads EVERY secret — the ServiceNow credential, the chat
    # webhooks, the steward's database URL — for a service that needs exactly two. The
    # grants are per-secret instead, next to the service that uses them.
    mcp_auth = { display = "MCP OAuth proxy — authorization server (Cloud Run)", roles = [
      "roles/datastore.user",
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
    # Anonymous Ask-the-Data tier (ADR-0019): impersonated by the BFF for guests.
    # Resource-level grants live outside this map: dataset READER on graph/gold/
    # loans, maskedReader on the PII_FINANCIAL data policy, and tokenCreator for
    # the BFF SA on this SA. Deliberately NO fine-grained policy-tag read.
    analyst_anon = { display = "Anonymous analyst tier (masked reader)", roles = [
      "roles/bigquery.jobUser",
      "roles/geminidataanalytics.dataAgentStatelessUser",
      "roles/geminidataanalytics.dataAgentUser",
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
      # Read-only access so the `terraform plan` CI check can refresh state across all
      # modules (datasets/pubsub/dlp/catalog/etc.). Apply is still run locally; the CI
      # SA never gets write/editor here.
      "roles/viewer",
      # basic Viewer covers *.get and *.list but NOT *.getIamPolicy, so refreshing any
      # google_*_iam_member resource 403s during plan. Surfaced when the controls-alerting
      # module added a Pub/Sub topic IAM binding (ADR-0026). securityReviewer is the
      # read-only role that grants policy reads; it adds no write capability.
      "roles/iam.securityReviewer",
      "roles/serviceusage.serviceUsageConsumer", # x-goog-user-project quota for plan
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

# The CI/CD identity minting an OIDC id-token FOR ITSELF, so a scheduled job can call
# the private services it deploys (ADR-0031 verification).
#
# `gcloud auth print-identity-token` works for a signed-in human and NOT under Workload
# Identity Federation — external-account credentials have no id-token to print, and the
# error says only "No identity token can be obtained from the current credentials". So
# CI mints one explicitly through the IAM Credentials API, which needs tokenCreator.
#
# Scoped to this ONE service account, not granted at project level: project-level
# tokenCreator would let the deploy identity impersonate every service account in the
# platform, including the ones that read customer data. Here it can only be itself.
resource "google_service_account_iam_member" "cicd_self_token_creator" {
  service_account_id = google_service_account.sa["cicd"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.sa["cicd"].email}"
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
    # The Budget API stores the project NUMBER and returns it on read, so writing the
    # project ID here produced a permanent diff: every plan proposed swapping
    # "projects/strongsville-city-schools" back in, and every apply wrote it and read
    # the number out again. Resolve the number instead of arguing with the API.
    projects = ["projects/${data.google_project.this.number}"]
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
