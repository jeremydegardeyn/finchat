# Materialized views with scheduled refresh for hot serving rollups (the sandbox
# uses logical views recomputed per query). Representative MV over the gold layer.
# Reference overlay — not applied.

variable "project_id" { type = string }
variable "gold_dataset" {
  type        = string
  description = "Existing gold dataset id the MV is created in."
}

resource "google_bigquery_table" "customer_360_mv" {
  project             = var.project_id
  dataset_id          = var.gold_dataset
  table_id            = "customer_360_mv"
  deletion_protection = false

  materialized_view {
    enable_refresh      = true
    refresh_interval_ms = 1800000 # 30 min
    query               = <<-SQL
      SELECT
        customer_id,
        ANY_VALUE(segment) AS segment,
        COUNT(*)           AS transaction_count,
        SUM(amount)        AS total_amount
      FROM `${var.project_id}.${var.gold_dataset}.fact_transaction`
      GROUP BY customer_id
    SQL
  }
}

output "mv_id" { value = google_bigquery_table.customer_360_mv.id }
