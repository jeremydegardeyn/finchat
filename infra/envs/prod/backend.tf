# Remote state in GCS (create the bucket once via scripts/bootstrap_state.sh).
# Bucket name is environment-specific so state never crosses environments.
terraform {
  backend "gcs" {
    bucket = "finchat-prod-tfstate"
    prefix = "infra"
  }
}
