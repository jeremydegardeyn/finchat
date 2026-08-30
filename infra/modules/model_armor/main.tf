###############################################################################
# Model Armor module — runtime safety/security screening for LLM I/O.
# Screens prompts + responses for prompt injection / jailbreak, sensitive data,
# malicious URLs, and harmful content. Complements DLP (data-at-rest governance).
# Called by the UI BFF on the agent path (ADR-0008).
###############################################################################

resource "google_model_armor_template" "this" {
  provider    = google-beta
  location    = var.region
  template_id = "${var.name_prefix}-${var.env}-armor"

  template_metadata {
    ignore_partial_invocation_failures = true
    log_sanitize_operations            = true
    log_template_operations            = true
  }

  filter_config {
    # Responsible-AI content filters.
    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = var.confidence_level
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = var.confidence_level
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = var.confidence_level
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = var.confidence_level
      }
    }

    # Prompt injection & jailbreak detection (low threshold = catch more).
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "LOW_AND_ABOVE"
    }

    # Malicious URL detection in prompts/responses.
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    # Sensitive Data Protection. Basic = built-in infoTypes; advanced = this project's
    # own DLP templates, so ingest and chat enforce the identical PII policy (ADR-0026).
    sdp_settings {
      dynamic "basic_config" {
        for_each = var.inspect_template == "" ? [1] : []
        content {
          filter_enforcement = "ENABLED"
        }
      }
      dynamic "advanced_config" {
        for_each = var.inspect_template == "" ? [] : [1]
        content {
          inspect_template    = var.inspect_template
          deidentify_template = var.deidentify_template == "" ? null : var.deidentify_template
        }
      }
    }
  }
}

# Optional org/project minimum-enforcement floor (defense in depth).
resource "google_model_armor_floorsetting" "this" {
  count    = var.enable_floor_setting ? 1 : 0
  provider = google-beta
  location = "global"
  parent   = "projects/${var.project_id}"

  enable_floor_setting_enforcement = true

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "LOW_AND_ABOVE"
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
  }
}

# --- Model Armor -> DLP access (advanced SDP mode only) ----------------------
# In advanced mode Model Armor calls DLP on our behalf, as its own service agent — not
# as the caller. Without these grants screening fails at request time, not at apply time.
data "google_project" "this" {
  count      = var.inspect_template == "" ? 0 : 1
  project_id = var.project_id
}

locals {
  armor_agent = var.inspect_template == "" ? "" : "serviceAccount:service-${data.google_project.this[0].number}@gcp-sa-modelarmor.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "armor_dlp_user" {
  count   = var.inspect_template == "" ? 0 : 1
  project = var.project_id
  role    = "roles/dlp.user"
  member  = local.armor_agent
}

resource "google_project_iam_member" "armor_dlp_reader" {
  count   = var.inspect_template == "" ? 0 : 1
  project = var.project_id
  role    = "roles/dlp.reader"
  member  = local.armor_agent
}
