###############################################################################
# IAM module — custom least-privilege roles (job-function scoped)
# Predefined roles are often too broad for a bank; these narrow to exact verbs.
###############################################################################

locals {
  suffix = title(var.env)
}

# DaaS API: read curated Gold + run query jobs, nothing else.
resource "google_project_iam_custom_role" "daas_reader" {
  project     = var.project_id
  role_id     = "${var.name_prefix}_daas_reader_${var.env}"
  title       = "FinChat DaaS Reader ${local.suffix}"
  description = "Read-only access to Gold serving data for DaaS APIs."
  permissions = [
    "bigquery.datasets.get",
    "bigquery.tables.get",
    "bigquery.tables.getData",
    "bigquery.jobs.create",
  ]
}

# Loan approver: read loan/credit/risk, append decisions (no delete/update -> append-only audit).
resource "google_project_iam_custom_role" "loan_approver" {
  project     = var.project_id
  role_id     = "${var.name_prefix}_loan_approver_${var.env}"
  title       = "FinChat Loan Approver ${local.suffix}"
  description = "Review loan requests and append immutable approval decisions."
  permissions = [
    "bigquery.tables.get",
    "bigquery.tables.getData",
    "bigquery.tables.updateData", # INSERT-only enforced by table design (append)
    "bigquery.jobs.create",
    # NOTE: workflowexecutions.* permissions are not supported in custom roles;
    # grant the predefined roles/workflows.invoker separately if an approver
    # needs to resolve workflow callbacks.
  ]
}

# Pipeline operator: manage ingestion artifacts without admin on the project.
resource "google_project_iam_custom_role" "pipeline_operator" {
  project     = var.project_id
  role_id     = "${var.name_prefix}_pipeline_operator_${var.env}"
  title       = "FinChat Pipeline Operator ${local.suffix}"
  description = "Launch/drain Dataflow and inspect Pub/Sub without broad admin."
  permissions = [
    "dataflow.jobs.create",
    "dataflow.jobs.cancel",
    "dataflow.jobs.get",
    "dataflow.jobs.list",
    "pubsub.subscriptions.consume",
    "pubsub.subscriptions.get",
    "pubsub.topics.publish",
  ]
}
