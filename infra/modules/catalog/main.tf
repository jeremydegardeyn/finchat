###############################################################################
# Knowledge Catalog (Dataplex Universal Catalog) module — ADR-0011 / docs/12.
# Overlay only: aspect types (metadata model), business-domain entry groups,
# and data-quality/profile scans that publish DQ scores back to the catalog.
# No change to BigQuery storage, pipelines, or serving.
###############################################################################

locals {
  prefix = "${var.name_prefix}-${var.env}"

  # Aspect Types = the structured metadata model attached to catalog entries.
  aspect_types = {
    "data-product" = {
      display = "FinChat Data Product"
      fields = [
        { name = "business_domain", type = "string", index = 1 },
        { name = "product_owner", type = "string", index = 2 },
        { name = "steward", type = "string", index = 3 },
        { name = "criticality", type = "enum", index = 4, enumValues = [
          { name = "CRITICAL", index = 1 }, { name = "HIGH", index = 2 },
        { name = "MEDIUM", index = 3 }, { name = "LOW", index = 4 }] },
        { name = "certification_status", type = "enum", index = 5, enumValues = [
          { name = "CERTIFIED", index = 1 }, { name = "CANDIDATE", index = 2 },
        { name = "DEPRECATED", index = 3 }] },
        { name = "sla", type = "string", index = 6 },
        { name = "cost_center", type = "string", index = 7 },
      ]
    }
    "governance" = {
      display = "FinChat Governance"
      fields = [
        { name = "pii_classification", type = "enum", index = 1, enumValues = [
          { name = "PII_DIRECT", index = 1 }, { name = "PII_FINANCIAL", index = 2 },
          { name = "CONFIDENTIAL", index = 3 }, { name = "INTERNAL", index = 4 },
        { name = "PUBLIC", index = 5 }] },
        { name = "policy_tag_ref", type = "string", index = 2 },
        { name = "retention", type = "string", index = 3 },
        { name = "residency", type = "string", index = 4 },
      ]
    }
    "operational" = {
      display = "FinChat Operational"
      fields = [
        { name = "data_quality_score", type = "string", index = 1 },
        { name = "last_dq_run", type = "datetime", index = 2 },
        { name = "freshness_sla", type = "string", index = 3 },
        { name = "pipeline_version", type = "string", index = 4 },
      ]
    }
    "ai-asset" = {
      display = "FinChat AI Asset"
      fields = [
        { name = "asset_kind", type = "string", index = 1 },
        { name = "embedding_model", type = "string", index = 2 },
        { name = "grounding_for", type = "string", index = 3 },
        { name = "last_eval_scores", type = "string", index = 4 },
      ]
    }
  }
}

# Dataplex runs scans as its per-project service agent. To profile/quality-scan
# the silver transaction table it must read the PII_FINANCIAL-tagged columns
# (`amount`, `counterparty_account`), so grant that agent fine-grained reader on
# the tag — same CLS pattern as the DaaS API SA.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_data_catalog_policy_tag_iam_member" "scan_financial_reader" {
  count      = var.financial_policy_tag_id == "" ? 0 : 1
  policy_tag = var.financial_policy_tag_id
  role       = "roles/datacatalog.categoryFineGrainedReader"
  member     = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-dataplex.iam.gserviceaccount.com"
}

resource "google_dataplex_aspect_type" "types" {
  for_each = local.aspect_types
  project  = var.project_id
  # GLOBAL so the aspect types are usable by BigQuery catalog entries regardless of
  # region (BQ entries land in the 'us' multi-region; a us-central1 aspect type is
  # rejected as "not usable by entries in region 'us'").
  location       = "global"
  aspect_type_id = "${local.prefix}-${each.key}"
  display_name   = each.value.display
  labels         = var.labels
  metadata_template = jsonencode({
    name         = "${local.prefix}-${each.key}"
    type         = "record"
    recordFields = each.value.fields
  })
}

# One Entry Group per business domain (Customer, Deposits, Lending, ...).
resource "google_dataplex_entry_group" "domains" {
  for_each       = toset(var.domains)
  project        = var.project_id
  location       = var.region
  entry_group_id = "${local.prefix}-${each.value}"
  display_name   = title(each.value)
  description    = "FinChat ${title(each.value)} domain — data products."
  labels         = var.labels
}

# --- Data Quality + Profile scans (publish DQ scores to the catalog) ---------
resource "google_dataplex_datascan" "silver_txn_profile" {
  project      = var.project_id
  location     = var.region
  data_scan_id = "${local.prefix}-silver-txn-profile"
  labels       = var.labels
  depends_on   = [google_data_catalog_policy_tag_iam_member.scan_financial_reader]
  data {
    resource = "//bigquery.googleapis.com/projects/${var.project_id}/datasets/${var.silver_dataset}/tables/transaction"
  }
  execution_spec {
    trigger {
      on_demand {}
    }
  }
  data_profile_spec {}
}

resource "google_dataplex_datascan" "silver_txn_quality" {
  project      = var.project_id
  location     = var.region
  data_scan_id = "${local.prefix}-silver-txn-quality"
  labels       = var.labels
  depends_on   = [google_data_catalog_policy_tag_iam_member.scan_financial_reader]
  data {
    resource = "//bigquery.googleapis.com/projects/${var.project_id}/datasets/${var.silver_dataset}/tables/transaction"
  }
  execution_spec {
    trigger {
      on_demand {}
    }
  }
  data_quality_spec {
    rules {
      column    = "amount"
      dimension = "VALIDITY"
      non_null_expectation {}
    }
    rules {
      column    = "txn_type"
      dimension = "VALIDITY"
      set_expectation {
        values = ["DEPOSIT", "WITHDRAWAL", "TRANSFER", "FEE"]
      }
    }
    rules {
      column    = "idempotency_key"
      dimension = "UNIQUENESS"
      uniqueness_expectation {}
    }
  }
}
