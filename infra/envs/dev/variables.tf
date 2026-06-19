variable "project_id" {
  type    = string
  default = "strongsville-city-schools"
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "env" {
  type    = string
  default = "dev"
}
variable "name_prefix" {
  type    = string
  default = "finchat"
}

# --- Governance / ops --------------------------------------------------------
variable "privileged_group" {
  type        = string
  description = "Member allowed to read unmasked PII (e.g., group:fraud-ops@datadinosaur.com)."
  default     = ""
}
variable "masked_reader_member" {
  type        = string
  description = "Analyst-tier human granted maskedReader — sees NULL for PII_FINANCIAL (ADR-0019). Empty = only the anonymous SA is masked."
  default     = ""
}
variable "platform_admin_member" {
  type        = string
  description = "Platform Admin human granted dataplex.viewer to browse Data Products and request access. Empty = no grant."
  default     = ""
}
variable "notification_email" {
  type    = string
  default = ""
}

# --- Cost guardrails ---------------------------------------------------------
variable "enable_budget" {
  type    = bool
  default = false
}
variable "billing_account" {
  type    = string
  default = ""
}
variable "budget_amount_usd" {
  type    = number
  default = 25
}

# --- Enterprise toggles (default OFF -> near-zero cost) ----------------------
variable "enable_streaming_job" {
  type        = bool
  description = "Deploy persistent 24/7 Dataflow streaming job (enterprise). Off = on-demand."
  default     = false
}
variable "run_min_instances" {
  type        = number
  description = "Cloud Run min instances. 0 = scale-to-zero (sandbox)."
  default     = 0
}
variable "enable_api_gateway" {
  type        = bool
  description = "Deploy API Gateway (requires products/transactions/api/openapi.yaml from Increment 3)."
  default     = false
}
variable "enable_workflows" {
  type        = bool
  description = "Deploy loan Cloud Workflow (requires products/loans/workflow source from Increment 4)."
  default     = false
}

# --- Model Armor (LLM I/O screening) -----------------------------------------
variable "enable_model_armor" {
  type        = bool
  description = "Create a Model Armor template for agent prompt/response screening."
  default     = true
}
variable "enable_model_armor_floor" {
  type        = bool
  description = "Also create a project-level Model Armor floor setting (needs elevated perms)."
  default     = false
}

# --- Knowledge Catalog (Dataplex Universal Catalog) — ADR-0010 ---------------
variable "enable_catalog" {
  type        = bool
  description = "Deploy the Dataplex catalog overlay (aspect types, domain entry groups, DQ scans)."
  default     = false
}

# --- Custom domain -----------------------------------------------------------
variable "custom_domain" {
  type        = string
  description = "Map this domain to the UI Cloud Run service (e.g. finchat.datadinosaur.com). Empty = skip. Domain must be verified for the project first."
  default     = ""
}

variable "labels" {
  type    = map(string)
  default = { app = "finchat", managed_by = "terraform" }
}

variable "enable_bigtable" {
  type        = bool
  description = "Deploy the Bigtable hot-path serving tier (ADR-0017). No scale-to-zero (~$475/mo/node) — default off; develop against the emulator."
  default     = false
}

variable "enable_agent_harness" {
  type        = bool
  description = "Deploy the durable agent harness / reconciliation steward (ADR-0021): Cloud Run (scale-to-zero) + Cloud SQL Postgres. Cloud SQL has no scale-to-zero — default off; develop against a local Postgres."
  default     = false
}
