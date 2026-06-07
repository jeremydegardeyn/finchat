###############################################################################
# Cloud Run custom domain mapping (near-zero cost; ADR-0007 hosting).
# Maps e.g. finchat.datadinosaur.com -> the UI Cloud Run service and provisions
# a Google-managed TLS cert. Outputs the DNS records to add at the registrar.
#
# PREREQ: the domain must be verified for this project (Search Console /
# `gcloud domains verify`) before apply, or creation fails.
#
# Enterprise target: a Global External HTTPS Load Balancer + serverless NEG +
# managed cert + Cloud Armor (documented in docs/10); same DNS swap.
###############################################################################

resource "google_cloud_run_domain_mapping" "this" {
  location = var.region
  name     = var.domain

  metadata {
    namespace = var.project_id
  }
  spec {
    route_name = var.service_name
  }
}
