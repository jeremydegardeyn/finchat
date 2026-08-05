###############################################################################
# Agent registry module (ADR-0023)
#
# Turns the agent registry from an inventory into a control by giving it teeth in
# three places:
#
#   1. Identity   — one service account per agent, never shared, so every action in
#                   the audit trail resolves to a single agent and its human owner.
#   2. Delegation — runtime services hold no agent privileges; they impersonate the
#                   acting agent's identity for the duration of a tool call. Same
#                   pattern as the anonymous analyst tier (ADR-0019).
#   3. Evidence   — a BigQuery registry table (who owns what, recertified when) and
#                   an append-only action log keyed by agent identity.
#
# The `agents` map is generated from scripts/agents_catalog.py. Editing it here is a
# drift bug; edit the catalogue and regenerate.
###############################################################################

locals {
  prefix = "${var.name_prefix}-${var.env}"

  # IAM caps account_id at 30 chars. Mirrors agents_catalog.service_account_id().
  sa_ids = {
    for id, a in var.agents : id => trimsuffix(substr("${local.prefix}-${a.sa_key}", 0, 30), "-")
  }
}

# --- Per-agent identities ----------------------------------------------------
resource "google_service_account" "agent" {
  for_each     = var.agents
  project      = var.project_id
  account_id   = local.sa_ids[each.key]
  display_name = "Agent: ${each.value.display} (${var.env})"
  description = join(" · ", [
    "Registered agent identity",
    "owner ${each.value.owner}",
    "risk ${each.value.risk_tier}",
    "recert due ${each.value.recert_due}",
  ])
}

# --- Delegation --------------------------------------------------------------
# Runtime services mint short-lived credentials for the acting agent rather than
# carrying its privileges. Flattened to (agent, member) pairs so a member added later
# does not force replacement of existing bindings.
locals {
  impersonation_pairs = merge([
    for id, _ in var.agents : {
      for m in var.impersonator_members : "${id}|${m}" => { agent = id, member = m }
    }
  ]...)
}

resource "google_service_account_iam_member" "impersonation" {
  for_each           = local.impersonation_pairs
  service_account_id = google_service_account.agent[each.value.agent].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value.member
}

# --- Evidence ----------------------------------------------------------------
resource "google_bigquery_dataset" "platform" {
  project                    = var.project_id
  dataset_id                 = "finchat_platform_${var.env}"
  friendly_name              = "FinChat platform control plane (${var.env})"
  description                = "Agent registry and agent action log. Evidence store for the AI control framework (docs/19, docs/20)."
  location                   = var.region
  labels                     = var.labels
  delete_contents_on_destroy = var.env != "prod"
}

# Current-state registry: one row per agent, refreshed by agent_registry_bootstrap.py
# on every deploy. Truth lives in the catalogue; this is the queryable projection that
# lets the Admin UI and an examiner ask "what is running and who owns it".
resource "google_bigquery_table" "agent_registry" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.platform.dataset_id
  table_id            = "agent_registry"
  deletion_protection = var.env == "prod"
  labels              = var.labels

  schema = jsonencode([
    { name = "agent_id", type = "STRING", mode = "REQUIRED", description = "Registry key from agents_catalog.py" },
    { name = "display", type = "STRING", mode = "NULLABLE" },
    { name = "product", type = "STRING", mode = "NULLABLE" },
    { name = "kind", type = "STRING", mode = "NULLABLE", description = "llm_agent | managed_agent" },
    { name = "runtime", type = "STRING", mode = "NULLABLE" },
    { name = "service_account", type = "STRING", mode = "REQUIRED", description = "Distinct identity this agent acts as" },
    { name = "owner", type = "STRING", mode = "REQUIRED", description = "Accountable human owner" },
    { name = "business_area", type = "STRING", mode = "NULLABLE", description = "Supervising business area" },
    { name = "risk_tier", type = "STRING", mode = "REQUIRED" },
    { name = "tools", type = "STRING", mode = "REPEATED", description = "Approved tool allow-list; CI asserts it matches code" },
    { name = "data_scope", type = "STRING", mode = "NULLABLE" },
    { name = "model_alias", type = "STRING", mode = "NULLABLE" },
    { name = "model_ref", type = "STRING", mode = "NULLABLE", description = "Row in docs/19 model inventory" },
    { name = "consequential", type = "BOOL", mode = "NULLABLE", description = "Action has a side effect on data or a customer" },
    { name = "hitl", type = "BOOL", mode = "NULLABLE", description = "Human-in-the-loop gate required" },
    { name = "registered", type = "DATE", mode = "NULLABLE" },
    { name = "last_recertified", type = "DATE", mode = "NULLABLE" },
    { name = "recert_due", type = "DATE", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "published_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

# Append-only attribution sink. Every consequential action an agent takes lands here
# keyed by the agent identity that took it and the human accountable for it — the
# "who authorised this" question, answerable without reading application logs.
resource "google_bigquery_table" "agent_action_log" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.platform.dataset_id
  table_id            = "agent_action_log"
  deletion_protection = var.env == "prod"
  labels              = var.labels

  time_partitioning {
    type  = "DAY"
    field = "ts"
  }
  clustering = ["agent_id", "tool"]

  schema = jsonencode([
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "agent_id", type = "STRING", mode = "REQUIRED" },
    { name = "service_account", type = "STRING", mode = "REQUIRED", description = "Identity that actually acted" },
    { name = "owner", type = "STRING", mode = "REQUIRED", description = "Accountable human at time of action" },
    { name = "tool", type = "STRING", mode = "NULLABLE", description = "Tool invoked; NULL for a generation-only turn" },
    { name = "authorized", type = "BOOL", mode = "REQUIRED", description = "Was the tool inside the registered allow-list" },
    { name = "consequential", type = "BOOL", mode = "NULLABLE" },
    { name = "hitl_approver", type = "STRING", mode = "NULLABLE", description = "Verified approver email where a gate applied" },
    { name = "session_id", type = "STRING", mode = "NULLABLE" },
    { name = "model_version", type = "STRING", mode = "NULLABLE", description = "Version that actually served (ADR-0022)" },
    { name = "outcome", type = "STRING", mode = "NULLABLE" },
  ])
}

# --- Dataset access ----------------------------------------------------------
resource "google_bigquery_dataset_iam_member" "readers" {
  for_each   = toset(var.registry_readers)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.platform.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = each.value
}

resource "google_bigquery_dataset_iam_member" "writers" {
  for_each   = toset(var.registry_writers)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.platform.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value
}
