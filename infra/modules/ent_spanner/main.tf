# Spanner payments ledger — the enterprise system of record. Multi-region config
# (external consistency via TrueTime), double-entry schema with hotspot-free PKs,
# entries interleaved in accounts for locality, and a change stream for CDC.
# Reference overlay — not applied.

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "spanner_config" {
  type    = string
  default = "nam3" # multi-region US
}
variable "processing_units" {
  type    = number
  default = 1000
}

resource "google_spanner_instance" "main" {
  project          = var.project_id
  name             = "${var.name_prefix}-${var.env}-ledger"
  config           = var.spanner_config
  display_name     = "FinChat enterprise ledger"
  processing_units = var.processing_units
}

resource "google_spanner_database" "payments" {
  project                  = var.project_id
  instance                 = google_spanner_instance.main.name
  name                     = "payments"
  version_retention_period = "7d"
  deletion_protection      = true

  ddl = [
    # Accounts. PK is a UUID/hash — never a monotonic key, to avoid write hotspots.
    <<-SQL
      CREATE TABLE accounts (
        account_id  STRING(36) NOT NULL,
        customer_id STRING(36) NOT NULL,
        currency    STRING(3)  NOT NULL,
        status      STRING(16) NOT NULL,
        opened_at   TIMESTAMP  NOT NULL OPTIONS (allow_commit_timestamp = true),
      ) PRIMARY KEY (account_id)
    SQL
    ,
    # Double-entry ledger lines, interleaved in the parent account for locality.
    # Money is INT64 micros (never a float). Every transaction is balanced across
    # its DEBIT/CREDIT lines in a single read-write transaction (external consistency).
    <<-SQL
      CREATE TABLE ledger_entries (
        account_id    STRING(36) NOT NULL,
        entry_id      STRING(36) NOT NULL,
        txn_id        STRING(36) NOT NULL,
        direction     STRING(6)  NOT NULL,
        amount_micros INT64      NOT NULL,
        currency      STRING(3)  NOT NULL,
        booked_at     TIMESTAMP  NOT NULL OPTIONS (allow_commit_timestamp = true),
      ) PRIMARY KEY (account_id, entry_id),
        INTERLEAVE IN PARENT accounts ON DELETE CASCADE
    SQL
    ,
    # Fetch both sides of a transaction by txn_id (double-entry pairing / audit).
    "CREATE INDEX entries_by_txn ON ledger_entries(txn_id)",
    # CDC to downstream consumers (BigQuery medallion, fraud features).
    "CREATE CHANGE STREAM ledger_changes FOR accounts, ledger_entries",
  ]
}

output "instance_name" { value = google_spanner_instance.main.name }
output "database_name" { value = google_spanner_database.payments.name }
