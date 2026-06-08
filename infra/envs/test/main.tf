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
  labels = local.labels
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
