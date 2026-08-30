variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "enable_floor_setting" {
  type        = bool
  description = "Also create a project-level floor setting (org-wide minimum screening). Needs elevated perms."
  default     = false
}

variable "confidence_level" {
  type        = string
  description = "Detection sensitivity for RAI/PI filters."
  default     = "MEDIUM_AND_ABOVE"
}

# --- Sensitive Data Protection: advanced mode (ADR-0026) ----------------------
# Empty keeps the basic built-in infoType set. Supplying the project's own DLP inspect
# template makes ONE PII policy govern both enforcement points — Dataflow at ingest and
# Model Armor on chat I/O — instead of two definitions that drift.
#
# Must be the locations-qualified form (projects/P/locations/L/inspectTemplates/T);
# a global template cannot be referenced here at all.
variable "inspect_template" {
  type    = string
  default = ""
}

# Optional, and it changes the behaviour rather than just the detection: with an inspect
# template alone Model Armor reports a match, but with a de-identify template it also
# returns the redacted text in deidentifyResult.data.text — so the chat path can
# redact-and-continue instead of hard-blocking, using the same FINCHAT_TOKEN surrogates
# as the Silver tables.
variable "deidentify_template" {
  type    = string
  default = ""
}
