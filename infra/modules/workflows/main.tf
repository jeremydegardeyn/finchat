###############################################################################
# Workflows module — long-running loan orchestration (ADR-0005)
# Cloud Workflows supports native callbacks -> ideal for human-in-the-loop waits.
###############################################################################

resource "google_workflows_workflow" "loan" {
  project         = var.project_id
  region          = var.region
  name            = "${var.name_prefix}-${var.env}-loan-approval"
  description     = "Long-running loan approval orchestration with HITL callback."
  service_account = var.service_account
  source_contents = var.workflow_source
  labels          = var.labels
  call_log_level  = "LOG_ALL_CALLS"
  user_env_vars   = var.env_vars
}
