variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

# Generated from scripts/agents_catalog.py — never hand-edited.
#   python scripts/agents_catalog.py --env <env> --emit-tfvars infra/envs/<env>/agents.auto.tfvars.json
# The catalogue is the source of truth; this map is its Terraform projection, so the
# service-account set cannot drift from the registry.
variable "agents" {
  type = map(object({
    sa_key     = string
    display    = string
    owner      = string
    risk_tier  = string
    product    = string
    recert_due = string
  }))
  description = "Agent registry, generated from scripts/agents_catalog.py."
  default     = {}
}

# Runtime service accounts permitted to mint short-lived credentials for an agent
# identity. This is the same impersonation pattern the anonymous analyst tier already
# uses (foundation.analyst_anon): the process holds no agent privileges of its own, it
# exchanges its own identity for the acting agent's when that agent invokes a tool.
variable "impersonator_members" {
  type        = list(string)
  description = "Members granted roles/iam.serviceAccountTokenCreator on every agent identity."
  default     = []
}

variable "registry_readers" {
  type        = list(string)
  description = "Members granted read on the registry dataset (Admin UI BFF, CI)."
  default     = []
}

variable "registry_writers" {
  type        = list(string)
  description = "Members permitted to publish registry rows and append action-log entries."
  default     = []
}

variable "labels" {
  type    = map(string)
  default = {}
}
