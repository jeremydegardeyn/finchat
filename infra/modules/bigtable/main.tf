###############################################################################
# Bigtable module — operational hot-path serving (ADR-0017). DEFAULT OFF.
# Bigtable has no scale-to-zero (≈$475/mo/node), so the sandbox keeps
# enable_bigtable=false and develops against the local emulator; this module is
# the enterprise serving tier for single-digit-ms reads at high QPS.
###############################################################################

resource "google_bigtable_instance" "hot" {
  project             = var.project_id
  name                = "${var.name_prefix}-${var.env}-hot"
  deletion_protection = false # sandbox: allow teardown; enterprise: true

  cluster {
    cluster_id   = "${var.name_prefix}-${var.env}-hot-c1"
    zone         = "${var.region}-a"
    num_nodes    = var.nodes
    storage_type = "SSD"
  }

  labels = var.labels
}

# Recent transactions per account. Row key: account_id#reverse_ts#txn_suffix —
# "latest N for an account" becomes a prefix scan returning newest-first, and
# the high-cardinality account_id prefix spreads load (no hotspotting).
resource "google_bigtable_table" "txn_by_account" {
  project       = var.project_id
  instance_name = google_bigtable_instance.hot.name
  name          = "txn_by_account"

  column_family {
    family = "txn"
  }
}

# Current balance per account. Row key: account_id (point read).
resource "google_bigtable_table" "account_balance" {
  project       = var.project_id
  instance_name = google_bigtable_instance.hot.name
  name          = "account_balance"

  column_family {
    family = "bal"
  }
}

# Garbage collection: keep transactions 30 days in the hot path (BigQuery holds
# full history); balance keeps only the latest cell version.
resource "google_bigtable_gc_policy" "txn_ttl" {
  project         = var.project_id
  instance_name   = google_bigtable_instance.hot.name
  table           = google_bigtable_table.txn_by_account.name
  column_family   = "txn"
  deletion_policy = "ABANDON"

  max_age {
    duration = "720h" # 30 days
  }
}

resource "google_bigtable_gc_policy" "bal_latest" {
  project         = var.project_id
  instance_name   = google_bigtable_instance.hot.name
  table           = google_bigtable_table.account_balance.name
  column_family   = "bal"
  deletion_policy = "ABANDON"

  max_version {
    number = 1
  }
}

# The DaaS API reads the hot path.
resource "google_bigtable_instance_iam_member" "readers" {
  for_each = toset(var.reader_members)
  project  = var.project_id
  instance = google_bigtable_instance.hot.name
  role     = "roles/bigtable.reader"
  member   = each.value
}

# The pipeline / backfill writes it.
resource "google_bigtable_instance_iam_member" "writers" {
  for_each = toset(var.writer_members)
  project  = var.project_id
  instance = google_bigtable_instance.hot.name
  role     = "roles/bigtable.user"
  member   = each.value
}
