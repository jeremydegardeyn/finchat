# Project-level org-policy guardrails. Uses the v1 google_project_organization_policy
# resource for readability (the newer google_org_policy_policy is the modern path).
# Reference overlay — not applied.

variable "project_id" { type = string }
variable "org_id" { type = string }
variable "domain" { type = string }

# Restrict IAM grants to the org's identity domain (no external members).
resource "google_project_organization_policy" "domain_restricted_sharing" {
  project    = var.project_id
  constraint = "iam.allowedPolicyMemberDomains"
  list_policy {
    # In practice: allow { values = ["<directory customer id>"] }. Deny-by-default here.
    allow { all = false }
  }
}

# No long-lived service-account keys (use Workload Identity / WIF instead).
resource "google_project_organization_policy" "disable_sa_keys" {
  project    = var.project_id
  constraint = "iam.disableServiceAccountKeyCreation"
  boolean_policy { enforced = true }
}

# No external IPs on VMs (private-only data plane).
resource "google_project_organization_policy" "vm_external_ip_off" {
  project    = var.project_id
  constraint = "compute.vmExternalIpAccess"
  list_policy {
    deny { all = true }
  }
}

# Require OS Login for SSH (auditable, IAM-gated).
resource "google_project_organization_policy" "require_os_login" {
  project    = var.project_id
  constraint = "compute.requireOsLogin"
  boolean_policy { enforced = true }
}

# Restrict resource locations to US (data residency).
resource "google_project_organization_policy" "resource_locations" {
  project    = var.project_id
  constraint = "gcp.resourceLocations"
  list_policy {
    allow { values = ["in:us-locations"] }
  }
}
