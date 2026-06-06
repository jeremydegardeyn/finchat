output "daas_reader_role" {
  value = google_project_iam_custom_role.daas_reader.id
}
output "loan_approver_role" {
  value = google_project_iam_custom_role.loan_approver.id
}
output "pipeline_operator_role" {
  value = google_project_iam_custom_role.pipeline_operator.id
}
