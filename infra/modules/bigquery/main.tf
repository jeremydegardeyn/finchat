###############################################################################
# BigQuery module — Medallion (Bronze/Silver/Gold) + governance
# - 3 datasets with retention defaults
# - Data Catalog taxonomy + policy tags (column-level security)
# - Representative Silver tables (partitioned, clustered, policy-tagged PII)
# - Gold authorized view (serving layer)
# - Row-level security policy (applied via BigQuery DDL job)
###############################################################################

locals {
  bronze = "${var.name_prefix}_bronze_${var.env}"
  silver = "${var.name_prefix}_silver_${var.env}"
  gold   = "${var.name_prefix}_gold_${var.env}"
  loans  = "${var.name_prefix}_loans_${var.env}"
}

# --- Datasets ----------------------------------------------------------------
resource "google_bigquery_dataset" "bronze" {
  project                         = var.project_id
  dataset_id                      = local.bronze
  location                        = var.region
  description                     = "Raw, immutable landing (replay/audit source of truth)."
  default_partition_expiration_ms = var.table_expiration_days * 24 * 60 * 60 * 1000
  labels                          = merge(var.labels, { layer = "bronze" })
}

resource "google_bigquery_dataset" "silver" {
  project     = var.project_id
  dataset_id  = local.silver
  location    = var.region
  description = "Cleansed, conformed, deduplicated, PII de-identified (canonical model)."
  labels      = merge(var.labels, { layer = "silver" })
}

resource "google_bigquery_dataset" "gold" {
  project     = var.project_id
  dataset_id  = local.gold
  location    = var.region
  description = "Business aggregates & serving views for APIs/agents."
  labels      = merge(var.labels, { layer = "gold" })
}

# Loan Approval data product. TF-managed (location = var.region) so it can never
# drift to the US multi-region — its tables are created from products/loans/
# schemas/ddl.sql, which inherit this dataset's region. (Previously the DDL's
# CREATE SCHEMA ran without --location and defaulted to US, breaking co-location
# with the rest of the medallion and with Dataplex Data Products.)
resource "google_bigquery_dataset" "loans" {
  project     = var.project_id
  dataset_id  = local.loans
  location    = var.region
  description = "Loan approval data product: requests, profiles, risk, decisions, audit."
  labels      = merge(var.labels, { layer = "product", domain = "lending" })
}

# Knowledge Graph semantic layer: native banking_graph property graph + the
# kg_relationships join schema + customer_360. Views only — pre-joins the medallion
# entities so Conversational Analytics has explicit, correct joins (e.g.
# transaction -> account -> customer by customer_id).
resource "google_bigquery_dataset" "graph" {
  project     = var.project_id
  dataset_id  = "${var.name_prefix}_graph_${var.env}"
  location    = var.region
  description = "FinChat knowledge graph: banking_graph property graph, kg_relationships join schema, customer_360."
  labels      = merge(var.labels, { layer = "semantic", domain = "graph" })
}

# Live evaluation (AgentOps): captured conversations + LLM-as-judge scores. The BFF
# (writes logs) and the scorer SA (reads logs, writes scores) get dataEditor. Tables
# created from scripts/eval_schema.sql.
resource "google_bigquery_dataset" "eval" {
  project     = var.project_id
  dataset_id  = "${var.name_prefix}_eval_${var.env}"
  location    = var.region
  description = "FinChat live evaluation: conversation_log, conversation_scores, eval_summary."
  labels      = merge(var.labels, { layer = "ops", domain = "eval" })
}

resource "google_bigquery_dataset_iam_member" "eval_writers" {
  for_each   = toset(var.eval_writer_members)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.eval.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value
}

# --- Bronze raw landing table (Pub/Sub BigQuery subscription target) ---------
# Canonical Pub/Sub->BQ schema: raw payload in `data`, metadata for lineage.
resource "google_bigquery_table" "bronze_transaction_event" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.bronze.dataset_id
  table_id            = "transaction_event"
  deletion_protection = false
  time_partitioning {
    type  = "DAY"
    field = "publish_time"
  }
  clustering = ["subscription_name"]
  schema = jsonencode([
    { name = "subscription_name", type = "STRING", mode = "NULLABLE" },
    { name = "message_id", type = "STRING", mode = "NULLABLE", description = "Pub/Sub message id (NK)" },
    { name = "publish_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "data", type = "STRING", mode = "NULLABLE", description = "Raw JSON transaction payload" },
    { name = "attributes", type = "STRING", mode = "NULLABLE", description = "Message attributes (JSON)" },
  ])
  labels = var.labels
}

# --- Data classification taxonomy (column-level security) --------------------
resource "google_data_catalog_taxonomy" "classification" {
  project                = var.project_id
  region                 = var.region
  display_name           = "${var.name_prefix}-classification-${var.env}"
  description            = "FinChat data classification policy tags."
  activated_policy_types = ["FINE_GRAINED_ACCESS_CONTROL"]
}

resource "google_data_catalog_policy_tag" "tags" {
  for_each = {
    pii_direct    = "Directly identifies a person (name, email, ssn, phone)."
    pii_financial = "Sensitive financial data (account number, amount, balance)."
    confidential  = "Internal sensitive (risk score, credit profile)."
  }
  taxonomy     = google_data_catalog_taxonomy.classification.id
  display_name = upper(each.key)
  description  = each.value
}

# Privileged readers may read columns behind policy tags (others get access-denied/masked).
resource "google_data_catalog_policy_tag_iam_member" "fine_grained_reader" {
  for_each   = var.privileged_group == "" ? {} : google_data_catalog_policy_tag.tags
  policy_tag = each.value.id
  role       = "roles/datacatalog.categoryFineGrainedReader"
  member     = var.privileged_group
}

# Serving SAs (e.g. the DaaS API) need fine-grained read on PII_FINANCIAL — the
# Gold balance/summary views aggregate `amount`, which is policy-tag protected.
# Column-level security is enforced even through authorized views, so the querying
# SA must be a fine-grained reader (not just have dataset access).
resource "google_data_catalog_policy_tag_iam_member" "financial_readers" {
  for_each   = toset(var.financial_reader_members)
  policy_tag = google_data_catalog_policy_tag.tags["pii_financial"].id
  role       = "roles/datacatalog.categoryFineGrainedReader"
  member     = each.value
}

# --- Dynamic data masking on PII_FINANCIAL (ADR-0019) -------------------------
# Returns NULL for PII_FINANCIAL-tagged columns to `maskedReader`s: the analyst
# tier keeps running aggregate SQL but never sees real values, non-readers are
# denied, and fine-grained readers (above) see clear values. CLS + masking is the
# hard, per-user control — enforced regardless of how the table is reached.
resource "google_bigquery_datapolicy_data_policy" "pii_financial_mask" {
  project          = var.project_id
  location         = var.region
  data_policy_id   = "${var.name_prefix}_${var.env}_pii_financial_mask"
  policy_tag       = google_data_catalog_policy_tag.tags["pii_financial"].id
  data_policy_type = "DATA_MASKING_POLICY"
  data_masking_policy {
    predefined_expression = "ALWAYS_NULL"
  }
}

resource "google_bigquery_datapolicy_data_policy_iam_member" "masked_readers" {
  for_each       = toset(var.masked_reader_members)
  project        = var.project_id
  location       = var.region
  data_policy_id = google_bigquery_datapolicy_data_policy.pii_financial_mask.data_policy_id
  role           = "roles/bigquerydatapolicy.maskedReader"
  member         = each.value
}

# --- Silver tables (partitioned, clustered, PII policy-tagged) ----------------
resource "google_bigquery_table" "silver_customer" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.silver.dataset_id
  table_id            = "customer"
  deletion_protection = false
  clustering          = ["segment", "customer_id"]
  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }
  schema = jsonencode([
    { name = "customer_id", type = "STRING", mode = "REQUIRED", description = "PK (UUID)" },
    { name = "customer_natural_key", type = "STRING", mode = "REQUIRED", description = "Govt-ID hash (NK)" },
    { name = "full_name", type = "STRING", mode = "NULLABLE", policyTags = { names = [google_data_catalog_policy_tag.tags["pii_direct"].id] } },
    { name = "email", type = "STRING", mode = "NULLABLE", policyTags = { names = [google_data_catalog_policy_tag.tags["pii_direct"].id] } },
    { name = "segment", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ingest_time", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "pipeline_version", type = "STRING", mode = "NULLABLE" },
  ])
  labels = var.labels
}

resource "google_bigquery_table" "silver_account" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.silver.dataset_id
  table_id            = "account"
  deletion_protection = false
  clustering          = ["customer_id", "account_type"]
  time_partitioning {
    type  = "DAY"
    field = "opened_at"
  }
  schema = jsonencode([
    { name = "account_id", type = "STRING", mode = "REQUIRED", description = "PK (UUID)" },
    { name = "account_number", type = "STRING", mode = "REQUIRED", policyTags = { names = [google_data_catalog_policy_tag.tags["pii_financial"].id] } },
    { name = "customer_id", type = "STRING", mode = "REQUIRED", description = "FK -> customer" },
    { name = "account_type", type = "STRING", mode = "REQUIRED" },
    { name = "currency", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "opened_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ingest_time", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "pipeline_version", type = "STRING", mode = "NULLABLE" },
  ])
  labels = var.labels
}

resource "google_bigquery_table" "silver_transaction" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.silver.dataset_id
  table_id            = "transaction"
  deletion_protection = false
  clustering          = ["account_id", "txn_type"]
  time_partitioning {
    type  = "DAY"
    field = "event_time"
  }
  schema = jsonencode([
    { name = "transaction_id", type = "STRING", mode = "REQUIRED", description = "PK (UUID)" },
    { name = "idempotency_key", type = "STRING", mode = "REQUIRED", description = "NK for MERGE dedup" },
    { name = "account_id", type = "STRING", mode = "REQUIRED", description = "FK -> account" },
    { name = "txn_type", type = "STRING", mode = "REQUIRED", description = "DEPOSIT|WITHDRAWAL|TRANSFER|FEE" },
    { name = "amount", type = "NUMERIC", mode = "REQUIRED", policyTags = { names = [google_data_catalog_policy_tag.tags["pii_financial"].id] } },
    { name = "currency", type = "STRING", mode = "REQUIRED" },
    { name = "counterparty_account", type = "STRING", mode = "NULLABLE", policyTags = { names = [google_data_catalog_policy_tag.tags["pii_financial"].id] } },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "event_time", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ingest_time", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "source_system", type = "STRING", mode = "NULLABLE" },
    { name = "pipeline_version", type = "STRING", mode = "NULLABLE" },
  ])
  labels = var.labels
}

# --- Gold serving view (authorized view; references Silver) -------------------
resource "google_bigquery_table" "gold_account_summary" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.gold.dataset_id
  table_id            = "account_summary"
  deletion_protection = false
  # View query is a string, so TF can't infer the dependency — make it explicit
  # (BigQuery validates referenced tables exist at view creation).
  depends_on = [
    google_bigquery_table.silver_account,
    google_bigquery_table.silver_transaction,
  ]
  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        a.account_id,
        a.customer_id,
        a.account_type,
        a.currency,
        a.status,
        COUNTIF(t.txn_type = 'DEPOSIT')     AS deposit_count,
        COUNTIF(t.txn_type = 'WITHDRAWAL')  AS withdrawal_count,
        COUNTIF(t.txn_type = 'FEE')         AS fee_count,
        SUM(CASE WHEN t.txn_type IN ('DEPOSIT')              THEN t.amount ELSE 0 END)
          - SUM(CASE WHEN t.txn_type IN ('WITHDRAWAL','FEE') THEN t.amount ELSE 0 END) AS net_balance,
        MAX(t.event_time) AS last_activity_at
      FROM `${var.project_id}.${local.silver}.account` a
      LEFT JOIN `${var.project_id}.${local.silver}.transaction` t USING (account_id)
      GROUP BY 1,2,3,4,5
    SQL
  }
  labels = var.labels
}

# Authorized view: let Gold view read Silver without granting consumers Silver access.
resource "google_bigquery_dataset_access" "gold_view_authorized_on_silver" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.silver.dataset_id
  view {
    project_id = var.project_id
    dataset_id = google_bigquery_dataset.gold.dataset_id
    table_id   = google_bigquery_table.gold_account_summary.table_id
  }
}

# --- Dataset IAM (least privilege) -------------------------------------------
resource "google_bigquery_dataset_iam_member" "gold_viewers" {
  for_each   = toset(var.viewer_members)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.gold.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = each.value
}

resource "google_bigquery_dataset_iam_member" "bronze_editors" {
  for_each   = toset(var.editor_members)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bronze.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value
}

resource "google_bigquery_dataset_iam_member" "silver_editors" {
  for_each   = toset(var.editor_members)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.silver.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value
}

# --- Row-level security (applied via DDL; provider has no RAP resource) -------
# CREATE OR REPLACE makes this idempotent; job_id re-hashes when the DDL changes.
resource "google_bigquery_job" "row_access_policy" {
  project = var.project_id
  job_id  = "rap-transaction-${substr(md5(local.rls_ddl), 0, 12)}"
  labels  = var.labels
  query {
    query              = local.rls_ddl
    use_legacy_sql     = false
    create_disposition = ""
    write_disposition  = ""
  }
  depends_on = [google_bigquery_table.silver_transaction]
}

locals {
  # Example RLS: customer-service reps see all rows; everyone else only non-PII test data.
  # Replace the filter / grantees with your enterprise group mapping.
  rls_ddl = <<-SQL
    CREATE OR REPLACE ROW ACCESS POLICY rap_active_accounts
    ON `${var.project_id}.${local.silver}.transaction`
    GRANT TO ("domain:datadinosaur.com")
    FILTER USING (status = 'POSTED');
  SQL
}
