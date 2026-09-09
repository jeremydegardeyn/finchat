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
  # Masked tier rides on the data-product access groups (joining a group via the
  # Dataplex access-request flow confers table dataViewer + maskedReader here).
  # PII-consuming groups only; ai-platform/support-agents read the KB (no PII tags).
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
  eval_writer_members = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["cicd"]}",
  ]
  labels = local.labels
}

# Platform Admin persona: browse Dataplex Data Products to request access (then
# CLS-denied on the actual data). dataplex.viewer grants NO column data access.
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
  # The process API composes over this one (ADR-0030); the API Gateway SA is added only
  # when the gateway is enabled.
  invokers = concat(
    ["serviceAccount:${module.foundation.service_account_emails["process"]}",
    "serviceAccount:${module.foundation.service_account_emails["mcp"]}"],
    var.enable_api_gateway ? ["serviceAccount:${module.foundation.service_account_emails["txn_api"]}"] : [],
  )
  labels = local.labels
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
    "serviceAccount:${module.foundation.service_account_emails["process"]}",
    "serviceAccount:${module.foundation.service_account_emails["mcp"]}",
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
  invokers = [
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    # The MCP server's knowledge-base tool calls the agent's retrieval-only
    # /search endpoint. Without this it degrades to local BM25 and says so.
    "serviceAccount:${module.foundation.service_account_emails["mcp"]}",
  ]
  labels = local.labels
}

# --- Process layer (ADR-0030) ------------------------------------------------
# Composes the transactions and loan domains and owns next_action — the business rule
# the web, mobile and agent channels share. Scale-to-zero like every other service here.
module "process_api" {
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-process"
  service_account = module.foundation.service_account_emails["process"]
  min_instances   = var.run_min_instances
  # Backend URLs are set by CI/CD alongside the image (the module ignores env drift for
  # exactly this reason), so Terraform provisions the shell and never fights the deploy.
  env_vars = {}
  # Invoked by the channels: the mobile experience API, the web BFF, and the MCP server
  # once it is deployed. All go through here rather than composing for themselves.
  invokers = [
    "serviceAccount:${module.foundation.service_account_emails["mobile"]}",
    "serviceAccount:${module.foundation.service_account_emails["txn_api"]}",
    "serviceAccount:${module.foundation.service_account_emails["mcp"]}",
  ]
  labels = local.labels
}

# --- Experience layer: the mobile channel (ADR-0030) -------------------------
# One screen, one round trip. Calls the process API and nothing else — LAYER-1 is a CI
# rule, and this is the same constraint expressed in IAM: it holds no invoker grant on
# any system API.
module "mobile_api" {
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-mobile"
  service_account = module.foundation.service_account_emails["mobile"]
  min_instances   = var.run_min_instances
  env_vars        = {}
  labels          = local.labels
}

# --- Experience layer: the agent channel over HTTP (ADR-0031) ----------------
# The same MCP server that runs locally over stdio, deployed so a *remote* client can
# reach it. Private, and deliberately so: MCP has no authentication of its own, and the
# spec's answer (OAuth + dynamic client registration, ADR-0020) is not built. Cloud Run
# IAM is the authentication here, which is why every caller must be named below.
#
# The identity trade this makes is worth stating rather than discovering: a stdio client
# authenticates as the *person* running it, so ADR-0019 column-level security is
# evaluated against them. This service authenticates as itself, so a remote caller sees
# what THIS service account is entitled to, not what the caller is. That is the honest
# limit of a service-identity MCP endpoint and the reason ADR-0020 still matters.
module "mcp_server" {
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-mcp"
  service_account = module.foundation.service_account_emails["mcp"]
  min_instances   = var.run_min_instances
  # Public only where a hosted client actually needs it, and only alongside the OAuth
  # env vars the deploy sets — see the variable's description for why the two are
  # deliberately separate switches.
  allow_unauthenticated = var.mcp_public
  # Backend URLs + the allowed-hosts allow-list are set by CI/CD alongside the image.
  env_vars = {}
  # Remote MCP clients. Empty by default: a service with no named callers is a service
  # nobody can reach, which is the correct default for an endpoint that exposes banking
  # tools. Each entry is one consuming workload's runtime identity — never a human, and
  # never a default compute SA, which several workloads share.
  invokers = var.mcp_client_service_accounts
  labels   = local.labels
}

# The two secrets the OAuth proxy reads, granted one at a time. The signing key is
# PER ENVIRONMENT: one key shared across all three would mean that reading it in dev
# lets you MINT a prod token — sign with prod's issuer and audience and prod's JWKS,
# publishing the same public key, accepts it. The audience and issuer checks stop a
# dev token being replayed at prod; they do not stop a dev key forging one.
# The Google client secret is genuinely shared: it is one OAuth client. Project-level
# secretAccessor for a service that needs two named secrets is the kind of over-grant
# nobody notices, because everything works either way.
resource "google_secret_manager_secret_iam_member" "mcp_auth_secrets" {
  for_each = var.enable_mcp_oauth ? toset(["finchat-oauth-client-secret",
  "${var.name_prefix}-${var.env}-mcp-oauth-signing-key"]) : toset([])
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${module.foundation.service_account_emails["mcp_auth"]}"
}

# --- An AWS workload calling the MCP endpoint (ADR-0020/0031) ----------------
# The OAuth flow needs a human at the Google step, which an unattended container in AWS
# does not have. Workload Identity Federation is the right shape instead: an AWS IAM role
# proves itself to GCP, impersonates the service account below, and mints a Google
# id-token audienced to the MCP service — which is exactly the `FINCHAT_MCP_SERVICE_CALLERS`
# path the AI gateway already uses. No new code, and no long-lived key anywhere.
#
# The identity trade is deliberate and worth naming: this is a SERVICE, so the audit says
# the service and `caller.require_staff` will refuse it from staff-only surfaces. That is
# correct — a machine is not staff — and it means an AWS harness exercises the account and
# knowledge-base tools, not free-form analytics.
#
# A SEPARATE pool from `finchat-gh-pool`: that one is CI's lifeline, its provider trusts
# GitHub's issuer, and an AWS provider has different attribute mapping and different
# blast radius. Sharing a pool to save a resource would put a new trust relationship
# inside the one that deploys this platform.
resource "google_iam_workload_identity_pool" "aws" {
  count                     = var.enable_aws_mcp_client ? 1 : 0
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-${var.env}-aws-pool"
  display_name              = "AWS workloads (${var.env})"
  description               = "Federates an AWS IAM role to call the MCP endpoint."
}

resource "google_iam_workload_identity_pool_provider" "aws" {
  count                              = var.enable_aws_mcp_client ? 1 : 0
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "${var.name_prefix}-${var.env}-aws-provider"
  display_name                       = "AWS account ${var.aws_account_id}"

  aws {
    account_id = var.aws_account_id
  }

  # Only the named role may federate. Without this condition ANY principal in the AWS
  # account could impersonate the service account — the account is the authentication
  # boundary, not the authorization one, and they are easy to conflate.
  attribute_condition = "attribute.aws_role == \"arn:aws:sts::${var.aws_account_id}:assumed-role/${var.aws_mcp_client_role}\""

  attribute_mapping = {
    "google.subject"        = "assertion.arn"
    "attribute.aws_role"    = "assertion.arn.extract(\"assumed-role/{role}/\") != \"\" ? \"arn:aws:sts::${var.aws_account_id}:assumed-role/\" + assertion.arn.extract(\"assumed-role/{role}/\") : assertion.arn"
    "attribute.aws_account" = "assertion.account"
  }
}

# Zero project roles, exactly like the `mcp` and `mcp_auth` identities. Everything this
# account may do is granted per-target: it appears in FINCHAT_MCP_SERVICE_CALLERS, and
# nothing else.
resource "google_service_account" "aws_mcp_client" {
  count        = var.enable_aws_mcp_client ? 1 : 0
  project      = var.project_id
  account_id   = "${var.name_prefix}-${var.env}-aws-mcp"
  display_name = "AWS workload calling the MCP endpoint (${var.env})"
}

resource "google_service_account_iam_member" "aws_mcp_client_federation" {
  count              = var.enable_aws_mcp_client ? 1 : 0
  service_account_id = google_service_account.aws_mcp_client[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.aws[0].name}/attribute.aws_role/arn:aws:sts::${var.aws_account_id}:assumed-role/${var.aws_mcp_client_role}"
}

# --- The OAuth proxy hosted MCP clients need (ADR-0020) ----------------------
# PUBLIC on purpose: a client calls /register, /authorize and /token before it has any
# credential at all, so IAM cannot be the gate here — the code is. That is why this
# service holds no data access, shares no modules with the platform, and fails closed
# when `OAUTH_ALLOWED_DOMAINS` and `OAUTH_ALLOWED_RESOURCES` are unset, which is how a
# provisioned-but-unconfigured environment behaves.
#
# It is NOT in the data path. Once a token is issued the client talks to the MCP server
# directly, so this service cannot add latency to a tool call or take tools down when it
# restarts.
module "mcp_auth" {
  count           = var.enable_mcp_oauth ? 1 : 0
  source          = "../../modules/cloud_run"
  project_id      = var.project_id
  region          = var.region
  service_name    = "${var.name_prefix}-${var.env}-mcp-auth"
  service_account = module.foundation.service_account_emails["mcp_auth"]
  min_instances   = var.run_min_instances
  env_vars        = {}
  # The one service in this platform that anonymous callers may reach.
  allow_unauthenticated = true
  labels                = local.labels
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
  # The deploy refreshes the platform-docs corpus (ADR-0024), which means writing this
  # dataset. Dataset-scoped, matching how the module beside it grants its writers.
  writer_members = [
    "serviceAccount:${module.foundation.service_account_emails["cicd"]}",
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
  chat_provider           = var.controls_chat_provider
}
