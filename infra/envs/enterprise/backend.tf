# Remote state in GCS. NOT bootstrapped — this overlay is reference-only and never
# applied. To make it real you would create the bucket (scripts/bootstrap_state.sh
# enterprise) and `terraform init`.
terraform {
  backend "gcs" {
    bucket = "finchat-enterprise-tfstate"
    prefix = "infra"
  }
}
