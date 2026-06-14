# Cloud Composer 2 (managed Airflow) — the enterprise orchestration substrate that
# replaces Cloud Workflows for complex, scheduled, multi-step DAGs. Private env on
# the VPC. Reference overlay — not applied. Sample DAG in ./dags.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "network_id" { type = string }
variable "subnet_id" { type = string }

resource "google_service_account" "composer" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-${var.env}-composer"
  display_name = "Cloud Composer environment SA"
}

resource "google_project_iam_member" "composer_worker" {
  project = var.project_id
  role    = "roles/composer.worker"
  member  = "serviceAccount:${google_service_account.composer.email}"
}

resource "google_composer_environment" "main" {
  provider = google-beta
  project  = var.project_id
  name     = "${var.name_prefix}-${var.env}-composer"
  region   = var.region

  config {
    software_config {
      image_version = "composer-2-airflow-2"
    }
    node_config {
      network         = var.network_id
      subnetwork      = var.subnet_id
      service_account = google_service_account.composer.email
    }
    private_environment_config {
      enable_private_endpoint = true
    }
  }
}

output "airflow_uri" { value = google_composer_environment.main.config[0].airflow_uri }
