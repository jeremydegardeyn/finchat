###############################################################################
# ENTERPRISE composition — reference overlay, NEVER applied. See /ENTERPRISE.md.
#
# Built in phases. Phase 1 (foundation) is wired below; later phases append the
# data / compute / orchestration / API / AI / observability modules.
###############################################################################

data "google_project" "host" {
  project_id = var.project_id
}

locals {
  labels = merge(var.labels, { env = var.env })
}

# === Phase 1 — Foundation =====================================================

# VPC, private subnet (+ GKE secondary ranges), Cloud NAT, PSC to Google APIs.
module "network" {
  source        = "../../modules/ent_network"
  project_id    = var.project_id
  region        = var.region
  name_prefix   = var.name_prefix
  env           = var.env
  subnet_cidr   = var.subnet_cidr
  pods_cidr     = var.pods_cidr
  services_cidr = var.services_cidr
}

# Customer-managed encryption keys for every data service.
module "cmek" {
  source         = "../../modules/ent_cmek"
  project_id     = var.project_id
  region         = var.region
  name_prefix    = var.name_prefix
  env            = var.env
  project_number = data.google_project.host.number
}

# Org-policy guardrails (domain-restricted sharing, no SA keys, no external IPs, …).
module "org_policies" {
  source     = "../../modules/ent_org_policies"
  project_id = var.project_id
  org_id     = var.org_id
  domain     = var.domain
}

# VPC Service Controls perimeter (only when an access policy id is supplied).
module "vpc_sc" {
  source           = "../../modules/ent_vpc_sc"
  count            = var.access_policy_id == "" ? 0 : 1
  project_number   = data.google_project.host.number
  access_policy_id = var.access_policy_id
  name_prefix      = var.name_prefix
  env              = var.env
}

# === Phase 2 — Data ===========================================================
# (BQ Editions reservation, Bigtable, Spanner, AlloyDB, materialized views)
# appended in a later commit.

# === Phase 3 — Compute & orchestration ========================================
# (GKE Autopilot, Cloud Composer 2)

# === Phase 4 — API & edge =====================================================
# (Apigee X, global HTTPS LB + Cloud CDN + Cloud Armor, IAP, Identity Platform)

# === Phase 5 — AI =============================================================
# (Vertex AI Vector Search, Provisioned Throughput)

# === Phase 6 — Observability ==================================================
# (SLOs, dashboards, log sinks → BigQuery)
