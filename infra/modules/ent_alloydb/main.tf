# AlloyDB for the operational/OLTP plane (loan origination) — high-performance
# PostgreSQL with a columnar engine (HTAP) and pgvector for in-database RAG. A
# primary plus a read pool, on the private VPC. Reference overlay — not applied.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "network_id" {
  type        = string
  description = "VPC self-link/id for private AlloyDB connectivity."
}

resource "google_alloydb_cluster" "main" {
  project    = var.project_id
  cluster_id = "${var.name_prefix}-${var.env}-alloydb"
  location   = var.region
  network_config {
    network = var.network_id
  }
}

resource "google_alloydb_instance" "primary" {
  cluster       = google_alloydb_cluster.main.name
  instance_id   = "${var.name_prefix}-${var.env}-primary"
  instance_type = "PRIMARY"
  machine_config {
    cpu_count = 4
  }
  database_flags = {
    "alloydb.enable_pgvector" = "on"
  }
}

resource "google_alloydb_instance" "read_pool" {
  cluster       = google_alloydb_cluster.main.name
  instance_id   = "${var.name_prefix}-${var.env}-read"
  instance_type = "READ_POOL"
  machine_config {
    cpu_count = 4
  }
  read_pool_config {
    node_count = 2
  }
  depends_on = [google_alloydb_instance.primary]
}

output "cluster_name" { value = google_alloydb_cluster.main.name }
