# Production Bigtable: replicated multi-cluster (HA) with autoscaling and a
# multi-cluster-routing app profile, plus the hot-path serving tables. Reference
# overlay — not applied. (Per-region CMEK omitted for brevity; production sets
# kms_key_name per cluster with a key in each cluster's region.)

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }

resource "google_bigtable_instance" "main" {
  project             = var.project_id
  name                = "${var.name_prefix}-${var.env}-bt"
  deletion_protection = true

  cluster {
    cluster_id   = "${var.name_prefix}-${var.env}-c1"
    zone         = "us-central1-b"
    storage_type = "SSD"
    autoscaling_config {
      min_nodes      = 1
      max_nodes      = 5
      cpu_target     = 60
      storage_target = 2560
    }
  }

  cluster {
    cluster_id   = "${var.name_prefix}-${var.env}-c2"
    zone         = "us-east1-c"
    storage_type = "SSD"
    autoscaling_config {
      min_nodes      = 1
      max_nodes      = 5
      cpu_target     = 60
      storage_target = 2560
    }
  }
}

# Route reads to the nearest healthy cluster; fail over automatically.
resource "google_bigtable_app_profile" "multi" {
  project                       = var.project_id
  instance                      = google_bigtable_instance.main.name
  app_profile_id                = "multi-cluster"
  multi_cluster_routing_use_any = true
  ignore_warnings               = true
}

# Newest-first transaction serving: row key account_id#reverse_ts#txn (prefix scan).
resource "google_bigtable_table" "txn_by_account" {
  project       = var.project_id
  instance_name = google_bigtable_instance.main.name
  name          = "txn_by_account"
  column_family {
    family = "t"
  }
}

# Point-read balance store.
resource "google_bigtable_table" "account_balance" {
  project       = var.project_id
  instance_name = google_bigtable_instance.main.name
  name          = "account_balance"
  column_family {
    family = "b"
  }
}

# Keep the serving rows small/fresh: GC to 1 version on the txn family.
resource "google_bigtable_gc_policy" "txn_versions" {
  project         = var.project_id
  instance_name   = google_bigtable_instance.main.name
  table           = google_bigtable_table.txn_by_account.name
  column_family   = "t"
  deletion_policy = "ABANDON"
  max_version {
    number = 1
  }
}

output "instance_name" { value = google_bigtable_instance.main.name }
