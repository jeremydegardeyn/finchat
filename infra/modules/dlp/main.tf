###############################################################################
# DLP module — inspection + de-identification templates
# Inspect template: detect PII; De-id template: mask/tokenize before Silver.
#
# ONE PII policy, TWO enforcement points (ADR-0026): the Dataflow pipeline applies these
# at ingest, and Model Armor's sdp_settings.advanced_config applies the same pair to chat
# I/O. That is why the templates are regional and why the ids are explicit — Model Armor
# needs a stable, locations-qualified name to point at.
###############################################################################

resource "google_data_loss_prevention_inspect_template" "pii" {
  parent = "projects/${var.project_id}/locations/${var.region}"
  # Explicit id: without it the server generates a random one, so nothing can reference the
  # template except through a Terraform output. Model Armor's template config is easier to
  # reason about against a predictable name, and this is the moment to fix it — the move to
  # a regional parent already forces a replacement.
  template_id  = "${var.name_prefix}-${var.env}-pii-inspect"
  display_name = "${var.name_prefix}-${var.env}-pii-inspect"
  description  = "Detects banking PII prior to Silver promotion, and in Model Armor chat screening."

  inspect_config {
    dynamic "info_types" {
      for_each = var.info_types
      content { name = info_types.value }
    }
    min_likelihood = "POSSIBLE"
    include_quote  = false
  }
}

resource "google_data_loss_prevention_deidentify_template" "mask" {
  parent       = "projects/${var.project_id}/locations/${var.region}"
  template_id  = "${var.name_prefix}-${var.env}-pii-deid"
  display_name = "${var.name_prefix}-${var.env}-pii-deid"
  # Also used by Model Armor: supplying a de-identify template (not just an inspect one)
  # makes Model Armor return de-identified text in deidentifyResult.data.text, so the chat
  # path can redact-and-continue instead of hard-blocking — and the FINCHAT_TOKEN surrogate
  # below means a tokenized value from chat matches the one in Silver.
  description = "Masks/tokenizes PII for Silver de-identification and Model Armor chat screening."

  deidentify_config {
    info_type_transformations {
      # Direct identifiers -> full masking.
      transformations {
        dynamic "info_types" {
          for_each = ["EMAIL_ADDRESS", "PERSON_NAME", "PHONE_NUMBER"]
          content { name = info_types.value }
        }
        primitive_transformation {
          character_mask_config {
            masking_character = "#"
          }
        }
      }
      # High-sensitivity identifiers -> deterministic crypto pseudonymization (joinable).
      transformations {
        dynamic "info_types" {
          for_each = ["US_SOCIAL_SECURITY_NUMBER", "CREDIT_CARD_NUMBER", "IBAN_CODE"]
          content { name = info_types.value }
        }
        primitive_transformation {
          crypto_deterministic_config {
            crypto_key {
              transient {
                name = "${var.name_prefix}-deid-key"
              }
            }
            # Required for deterministic crypto: the surrogate label wrapping the
            # tokenized value so it can be re-identified later.
            surrogate_info_type {
              name = "FINCHAT_TOKEN"
            }
          }
        }
      }
    }
  }
}
