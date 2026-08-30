###############################################################################
# DEV environment composition
# Wires platform modules. Enterprise toggles default OFF for near-zero cost.
###############################################################################

locals {
  labels = merge(var.labels, { env = var.env })
}

# --- Foundation: APIs, SAs, IAM, Artifact Registry, buckets, budget ----------
module "foundation" {
  source            = "../../modules/foundation"
  project_id        = var.project_id
  region            = var.region
  env               = var.env
  name_prefix       = var.name_prefix
  enable_budget     = var.enable_budget
  billing_account   = var.billing_account
  budget_amount_usd = var.budget_amount_usd
  labels            = local.labels
}

# --- Custom least-privilege roles --------------------------------------------
module "iam" {
  source      = "../../modules/iam"
  project_id  = var.project_id
  env         = var.env
  name_prefix = var.name_prefix
}

# --- BigQuery medallion + governance -----------------------------------------
module "bigquery" {
  source           = "../../modules/bigquery"
  project_id       = var.project_id
  region           = var.region
  env              = var.env
  name_prefix      = var.name_prefix
  privileged_group = var.privileged_group
  viewer_members = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["agent"]}",
  ]
  editor_members = [
    "serviceAccount:${module.foundation.service_account_emails["pipeline"]}",
    "serviceAccount:${module.foundation.service_account_emails["loan_api"]}",
  ]
  # DaaS API serves balances derived from the PII_FINANCIAL-tagged `amount` column,
  # so its SA must be a fine-grained reader on that tag (CLS enforced through views).
  financial_reader_members = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
  ]
  # Masked tier (NULL for PII_FINANCIAL + PII_DIRECT). Granted to the data-product
  # access GROUPS, so joining a group via the Dataplex access-request flow confers
  # the full masked-analyst tier (table dataViewer from the product + maskedReader
  # here) in one step — no per-person IAM. The anonymous SA covers staff who haven't
  # propagated an OAuth token yet; var.masked_reader_member is an optional extra
  # individual (now redundant for anyone in a group).
  masked_reader_members = compact([
    var.masked_reader_member,
    "group:crm-team@datadinosaur.com",
    "group:data-science@datadinosaur.com",
    "group:deposit-analysts@datadinosaur.com",
    "group:risk-analysts@datadinosaur.com",
    "group:collections-team@datadinosaur.com",
    "group:underwriting-team@datadinosaur.com",
    "serviceAccount:${module.foundation.service_account_emails["analyst_anon"]}",
  ])
  # Live eval: BFF (txn_api) writes conversation logs; CI/CD SA (the scorer runs via
  # the live-eval scheduled workflow) reads logs + writes scores.
  eval_writer_members = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["cicd"]}",
  ]
  labels = local.labels
}

# Platform Admin persona: browse the Dataplex Data Products page to request access
# (then CLS-denied on the actual data — the governance demo). dataplex.viewer covers
# the first-class dataProducts API and grants NO column data access.
resource "google_project_iam_member" "platform_admin_dataplex" {
  count   = var.platform_admin_member == "" ? 0 : 1
  project = var.project_id
  role    = "roles/dataplex.viewer"
  member  = var.platform_admin_member
}

# --- Pub/Sub eventing + DLQ + BQ subscription --------------------------------
module "pubsub" {
  source                 = "../../modules/pubsub"
  project_id             = var.project_id
  env                    = var.env
  name_prefix            = var.name_prefix
  bronze_table           = module.bigquery.bronze_transaction_event_table
  enable_bq_subscription = true
  labels                 = local.labels
}

# --- DLP inspect/de-id templates ---------------------------------------------
module "dlp" {
  source      = "../../modules/dlp"
  project_id  = var.project_id
  region      = var.region
  env         = var.env
  name_prefix = var.name_prefix
}

# --- Dataflow (on-demand by default; toggle for 24/7 streaming) ---------------
module "dataflow" {
  source                   = "../../modules/dataflow"
  project_id               = var.project_id
  region                   = var.region
  env                      = var.env
  name_prefix              = var.name_prefix
  dataflow_bucket          = module.foundation.dataflow_bucket
  pipeline_service_account = module.foundation.service_account_emails["pipeline"]
  input_subscription       = module.pubsub.dataflow_subscription
  silver_transaction_table = module.bigquery.silver_transaction_table
  dlq_topic                = module.pubsub.dlq_topic
  enable_streaming_job     = var.enable_streaming_job
  labels                   = local.labels
}

# --- Cloud Run services (scale-to-zero) --------------------------------------
module "txn_api" {
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-txn-api"
  service_account = module.foundation.service_account_emails["txn_api"]
  min_instances   = var.run_min_instances
  env_vars = {
    GCP_PROJECT     = var.project_id
    GOLD_DATASET    = module.bigquery.gold_dataset
    SILVER_DATASET  = module.bigquery.silver_dataset
    ACCOUNT_SUMMARY = module.bigquery.gold_account_summary
  }
  # When API Gateway is enabled, let its SA invoke this (private) service.
  invokers = var.enable_api_gateway ? ["serviceAccount:${module.foundation.service_account_emails["txn_api"]}"] : []
  labels   = local.labels
}

module "loan_api" {
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-loan-api"
  service_account = module.foundation.service_account_emails["loan_api"]
  min_instances   = var.run_min_instances
  env_vars = {
    GCP_PROJECT  = var.project_id
    GOLD_DATASET = module.bigquery.gold_dataset
  }
  # Invoked (OIDC) by the UI BFF (txn_api SA) and the banking agent (loan-status tool).
  invokers = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["agent"]}",
  ]
  labels = local.labels
}

module "agent" {
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-agent"
  service_account = module.foundation.service_account_emails["agent"]
  min_instances   = var.run_min_instances
  memory          = "1Gi" # ADK + Gemini agent; matches the CI/CD `--memory=1Gi` deploy flag.
  env_vars = {
    GCP_PROJECT = var.project_id
    REGION      = var.region
  }
  # UI BFF (runs as txn_api SA) invokes this private agent with an OIDC token.
  invokers = ["serviceAccount:${module.foundation.service_account_emails["txn_api"]}"]
  labels   = local.labels
}

module "ui" {
  source                = "../../modules/cloud_run"
  project_id            = var.project_id
  region                = var.region
  service_name          = "${var.name_prefix}-${var.env}-ui"
  service_account       = module.foundation.service_account_emails["txn_api"]
  min_instances         = var.run_min_instances
  allow_unauthenticated = true # demo UI; persona simulation handled in-app
  labels                = local.labels
}

# --- API Gateway (enabled once OpenAPI spec exists — Increment 3) -------------
module "api_gateway" {
  count                   = var.enable_api_gateway ? 1 : 0
  source                  = "../../modules/api_gateway"
  project_id              = var.project_id
  region                  = var.region
  env                     = var.env
  name_prefix             = var.name_prefix
  gateway_service_account = module.foundation.service_account_emails["txn_api"]
  openapi_spec = base64encode(templatefile("${path.module}/../../../products/transactions/api/openapi.gateway.yaml", {
    txn_api_url = module.txn_api.uri
  }))
}

# --- Loan Cloud Workflow (enabled once source exists — Increment 4) -----------
module "workflows" {
  count           = var.enable_workflows ? 1 : 0
  source          = "../../modules/workflows"
  project_id      = var.project_id
  region          = var.region
  env             = var.env
  name_prefix     = var.name_prefix
  service_account = module.foundation.service_account_emails["workflow"]
  workflow_source = file("${path.module}/../../../products/loans/workflow/loan_approval.yaml")
  env_vars = {
    LOAN_API_URL = module.loan_api.uri
    TXN_API_URL  = module.txn_api.uri
  }
}

# --- RAG knowledge base (BigQuery vector store) ------------------------------
module "rag" {
  source      = "../../modules/bigquery_rag"
  project_id  = var.project_id
  region      = var.region
  env         = var.env
  name_prefix = var.name_prefix
  reader_members = [
    "serviceAccount:${module.foundation.service_account_emails["agent"]}",
    # The BFF queries platform_chunks directly for the PLATFORM intent (docs/24). Reading
    # the table is not enough: ML.GENERATE_EMBEDDING invokes a REMOTE model through this
    # connection, so the caller also needs bigquery.connections.use. Without it the query
    # fails 403 — which reads as a permissions problem with the data, when it is actually
    # permission to use the embedding model.
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
  ]
  labels = local.labels
}

# --- Knowledge Catalog overlay (Dataplex Universal Catalog) ------------------
module "catalog" {
  count          = var.enable_catalog ? 1 : 0
  source         = "../../modules/catalog"
  project_id     = var.project_id
  region         = var.region
  env            = var.env
  name_prefix    = var.name_prefix
  silver_dataset = module.bigquery.silver_dataset
  # Lets the Dataplex scan service agent read policy-tag-protected columns it profiles.
  policy_tag_ids = module.bigquery.policy_tag_ids
  # Insights: one data-profile scan per product.
  profile_targets = [
    { id = "deposit-transactions", dataset = module.bigquery.silver_dataset, table = "transaction" },
    { id = "customer-master", dataset = module.bigquery.silver_dataset, table = "customer" },
    { id = "overdraft-history", dataset = module.bigquery.gold_dataset, table = "overdraft_history" },
    { id = "loan-master", dataset = module.bigquery.loans_dataset, table = "loan_status" },
    { id = "bank-knowledge-base", dataset = "${var.name_prefix}_kb_${var.env}", table = "kb_chunks" },
  ]
  labels = local.labels
}

# --- Model Armor (agent prompt/response screening) ---------------------------
module "model_armor" {
  count                = var.enable_model_armor ? 1 : 0
  source               = "../../modules/model_armor"
  project_id           = var.project_id
  region               = var.region
  env                  = var.env
  name_prefix          = var.name_prefix
  enable_floor_setting = var.enable_model_armor_floor
  # One PII policy across ingest and chat (ADR-0026). Empty vars fall back to basic mode.
  inspect_template    = var.model_armor_use_dlp_templates ? module.dlp.inspect_template : ""
  deidentify_template = var.model_armor_use_dlp_templates ? module.dlp.deidentify_template : ""
}

# --- Custom domain for the UI (e.g. finchat.datadinosaur.com) -----------------
module "ui_domain" {
  count        = var.custom_domain == "" ? 0 : 1
  source       = "../../modules/domain_mapping"
  project_id   = var.project_id
  region       = var.region
  domain       = var.custom_domain
  service_name = module.ui.service_name
}

# --- Monitoring + audit sink -------------------------------------------------
module "monitoring" {
  source              = "../../modules/monitoring"
  project_id          = var.project_id
  env                 = var.env
  name_prefix         = var.name_prefix
  notification_email  = var.notification_email
  dlq_subscription_id = "${var.name_prefix}-${var.env}-transactions-dlq-sub"
}

# --- Bigtable hot path (ADR-0017; default off — no scale-to-zero) -------------
module "bigtable" {
  count       = var.enable_bigtable ? 1 : 0
  source      = "../../modules/bigtable"
  project_id  = var.project_id
  region      = var.region
  env         = var.env
  name_prefix = var.name_prefix
  reader_members = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["agent"]}",
  ]
  writer_members = [
    "serviceAccount:${module.foundation.service_account_emails["pipeline"]}",
    "serviceAccount:${module.foundation.service_account_emails["cicd"]}",
  ]
  labels = local.labels
}

module "agent_harness" {
  count              = var.enable_agent_harness ? 1 : 0
  source             = "../../modules/agent_harness"
  project_id         = var.project_id
  region             = var.region
  env                = var.env
  name_prefix        = var.name_prefix
  run_sa_email       = module.foundation.service_account_emails["agent"]
  scheduler_sa_email = module.foundation.service_account_emails["workflow"]
  invoker_members = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["workflow"]}",
  ]
  labels = local.labels
}

# Agent registry (ADR-0023) — one identity per agent, impersonated by the runtimes,
# plus the registry + append-only action log. Service accounts and an empty dataset
# cost nothing, so unlike the Bigtable and steward toggles this is on by default: a
# control that ships disabled is not a control.
module "agent_registry" {
  source      = "../../modules/agent_registry"
  project_id  = var.project_id
  region      = var.region
  env         = var.env
  name_prefix = var.name_prefix
  agents      = var.agents

  # Runtimes that may mint short-lived credentials for an agent identity.
  impersonator_members = [
    "serviceAccount:${module.foundation.service_account_emails["agent"]}",
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["workflow"]}",
  ]
  registry_readers = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["cicd"]}",
  ]
  registry_writers = [
    "serviceAccount:${module.foundation.service_account_emails["cicd"]}",
    "serviceAccount:${module.foundation.service_account_emails["agent"]}",
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
  ]
  labels = local.labels
}

# --- Controls alerting (ADR-0026) --------------------------------------------
# Off by default: with both variables empty this module creates nothing at all.
module "controls_alerting" {
  source                  = "../../modules/controls_alerting"
  project_id              = var.project_id
  region                  = var.region
  env                     = var.env
  name_prefix             = var.name_prefix
  servicenow_instance_url = var.servicenow_instance_url
  servicenow_user         = var.servicenow_user
  evidence_dataset        = var.controls_evidence_dataset
}
