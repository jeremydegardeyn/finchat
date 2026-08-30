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
  description = <<-EOT
    Also create a project-level Model Armor floor setting — the minimum screening every
    caller gets whether or not the application asks for it. This is what turns screening
    from a convention into an enforced control: the gateway can be bypassed by unsetting
    an env var, a floor setting cannot.

    ENABLE IT IN EXACTLY ONE ENVIRONMENT. The resource is scoped to the PROJECT, and
    dev/test/prod share one project here (docs/26 F18), so two states with this set would
    each try to own the same singleton and fight over it on every apply. Prod owns it; the
    setting still covers dev and test because it is project-wide.

    Needs elevated permissions to create.
  EOT
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
