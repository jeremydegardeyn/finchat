# Customer-managed encryption keys (Cloud KMS) for every data service, with the
# per-service Google service agent granted encrypt/decrypt on its key. Reference
# overlay — not applied.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "project_number" {
  type        = string
  description = "Host project number, for the per-service Google service-agent bindings."
}

locals {
  keys = ["bigquery", "storage", "spanner", "bigtable", "pubsub"]
  # The managed service agent that encrypts/decrypts each service's data at rest.
  service_agents = {
    bigquery = "bq-${var.project_number}@bigquery-encryption.iam.gserviceaccount.com"
    storage  = "service-${var.project_number}@gs-project-accounts.iam.gserviceaccount.com"
    spanner  = "service-${var.project_number}@gcp-sa-spanner.iam.gserviceaccount.com"
    bigtable = "service-${var.project_number}@gcp-sa-bigtable.iam.gserviceaccount.com"
    pubsub   = "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  }
}

resource "google_kms_key_ring" "ring" {
  project  = var.project_id
  name     = "${var.name_prefix}-${var.env}-keyring"
  location = var.region
}

resource "google_kms_crypto_key" "key" {
  for_each        = toset(local.keys)
  name            = "${var.name_prefix}-${var.env}-${each.key}"
  key_ring        = google_kms_key_ring.ring.id
  rotation_period = "7776000s" # 90 days
  purpose         = "ENCRYPT_DECRYPT"
}

resource "google_kms_crypto_key_iam_member" "agent" {
  for_each      = local.service_agents
  crypto_key_id = google_kms_crypto_key.key[each.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${each.value}"
}

output "key_ids" { value = { for k, v in google_kms_crypto_key.key : k => v.id } }
