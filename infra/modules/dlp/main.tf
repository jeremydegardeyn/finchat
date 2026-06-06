###############################################################################
# DLP module — inspection + de-identification templates
# Inspect template: detect PII; De-id template: mask/tokenize before Silver.
# Pipeline references these by name (sampled in sandbox to control cost).
###############################################################################

resource "google_data_loss_prevention_inspect_template" "pii" {
  parent       = "projects/${var.project_id}"
  display_name = "${var.name_prefix}-${var.env}-pii-inspect"
  description  = "Detects banking PII prior to Silver promotion."

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
  parent       = "projects/${var.project_id}"
  display_name = "${var.name_prefix}-${var.env}-pii-deid"
  description  = "Masks/tokenizes PII for Silver de-identification."

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
          }
        }
      }
    }
  }
}
