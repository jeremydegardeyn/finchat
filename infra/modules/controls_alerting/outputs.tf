output "topic" {
  description = "Pub/Sub topic carrying control events to the dispatch workflow."
  value       = var.servicenow_instance_url == "" ? null : google_pubsub_topic.events[0].name
}

output "dlq_topic" {
  description = "Dead-letter topic for control events that could not be dispatched."
  value       = var.servicenow_instance_url == "" ? null : google_pubsub_topic.dlq[0].name
}

output "workflow" {
  description = "Dispatch workflow name."
  value       = var.servicenow_instance_url == "" ? null : google_workflows_workflow.dispatch[0].name
}

output "servicenow_secret" {
  description = "Secret Manager secret id for the ServiceNow password. Terraform creates the container; add the value out of band so it never enters state."
  value       = var.servicenow_instance_url == "" ? null : google_secret_manager_secret.servicenow[0].secret_id
}

output "evidence_dataset" {
  description = "Dataset the evidence sink writes to. Cloud Logging names the table itself (run_googleapis_com_stdout), so there is no fixed table name to output."
  value       = var.evidence_dataset == "" ? null : "${var.project_id}.${var.evidence_dataset}"
}
