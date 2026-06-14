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
# NOTE: the base medallion (BigQuery datasets, Pub/Sub, Dataplex) is reused from
# the existing `modules/*` unchanged and would also be composed here; these blocks
# are the enterprise *deltas* on top of it.

module "bq_reservation" {
  source         = "../../modules/ent_bq_reservation"
  project_id     = var.project_id
  location       = var.bq_multi_region
  name_prefix    = var.name_prefix
  env            = var.env
  baseline_slots = 100
  max_slots      = 500
}

module "bigtable" {
  source      = "../../modules/ent_bigtable"
  project_id  = var.project_id
  name_prefix = var.name_prefix
  env         = var.env
}

module "spanner" {
  source           = "../../modules/ent_spanner"
  project_id       = var.project_id
  name_prefix      = var.name_prefix
  env              = var.env
  spanner_config   = var.spanner_config
  processing_units = 1000
}

module "alloydb" {
  source      = "../../modules/ent_alloydb"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
  env         = var.env
  network_id  = module.network.network_id
}

module "materialized_views" {
  source       = "../../modules/ent_materialized_views"
  project_id   = var.project_id
  gold_dataset = "${var.name_prefix}_gold_${var.env}" # created by the reused base bigquery module
}

# === Phase 3 — Compute & orchestration ========================================

module "gke" {
  source              = "../../modules/ent_gke"
  project_id          = var.project_id
  region              = var.region
  name_prefix         = var.name_prefix
  env                 = var.env
  network_id          = module.network.network_id
  subnet_id           = module.network.subnet_id
  pods_range_name     = module.network.pods_range_name
  services_range_name = module.network.services_range_name
}

module "composer" {
  source      = "../../modules/ent_composer"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
  env         = var.env
  network_id  = module.network.network_id
  subnet_id   = module.network.subnet_id
}

# === Phase 4 — API & edge =====================================================

module "apigee" {
  source      = "../../modules/ent_apigee"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
  env         = var.env
  domain      = var.domain
  network_id  = module.network.network_id
}

module "edge" {
  source      = "../../modules/ent_edge"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
  env         = var.env
  domain      = var.domain
}

module "iap" {
  source             = "../../modules/ent_iap"
  project_id         = var.project_id
  support_email      = "platform@${var.domain}"
  staff_group        = "group:staff@${var.domain}"
  backend_service_id = module.edge.backend_service_id
}

module "identity_platform" {
  source     = "../../modules/ent_identity_platform"
  project_id = var.project_id
  domain     = var.domain
}

# === Phase 5 — AI =============================================================

module "vector_search" {
  source      = "../../modules/ent_vector_search"
  project_id  = var.project_id
  region      = var.region
  name_prefix = var.name_prefix
  env         = var.env
}

# Provisioned Throughput is a GSU capacity commitment (no Terraform resource) —
# see modules/ent_vertex_pt/README.md for what the enterprise tier buys + context caching.

# === Phase 6 — Observability ==================================================

module "observability" {
  source      = "../../modules/ent_observability"
  project_id  = var.project_id
  name_prefix = var.name_prefix
  env         = var.env
  domain      = var.domain
}
