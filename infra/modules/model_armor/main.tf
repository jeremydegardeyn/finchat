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

    # Sensitive Data Protection (basic DLP inspection of LLM I/O).
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
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
