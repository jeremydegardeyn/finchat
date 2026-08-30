variable "project_id" { type = string }
variable "env" { type = string }

# Templates are REGIONAL, not global (ADR-0026). Model Armor's sdp_settings.advanced_config
# only accepts the locations-qualified form `projects/P/locations/L/inspectTemplates/T`, so a
# global template (`projects/P/inspectTemplates/T`) cannot be referenced from it at all.
# Changing this on an existing deployment REPLACES both templates.
variable "region" {
  type    = string
  default = "us-central1"
}
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "info_types" {
  type        = list(string)
  description = "DLP infoTypes to inspect/de-identify."
  default     = ["EMAIL_ADDRESS", "PERSON_NAME", "PHONE_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "CREDIT_CARD_NUMBER", "IBAN_CODE"]
}
