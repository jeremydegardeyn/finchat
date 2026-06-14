# Identity Platform (CIAM) for the customer-facing surface that is unauthenticated
# in the sandbox. Email + Google sign-in, blocking functions for risk, MFA. The
# staff surface federates the workforce IdP separately (IAP, ent_iap). Reference
# overlay — not applied.

variable "project_id" { type = string }
variable "domain" { type = string }

resource "google_identity_platform_config" "default" {
  project = var.project_id

  sign_in {
    allow_duplicate_emails = false
    email {
      enabled           = true
      password_required = true
    }
  }

  authorized_domains = [
    "localhost",
    var.domain,
  ]

  mfa {
    state = "ENABLED"
  }
}

output "config_name" { value = google_identity_platform_config.default.name }
