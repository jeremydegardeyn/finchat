output "streaming_job_id" {
  description = "Persistent streaming job id (null when running on-demand)."
  value       = var.enable_streaming_job && var.template_spec_path != "" ? google_dataflow_flex_template_job.streaming[0].job_id : null
}

output "on_demand_launch_hint" {
  description = "gcloud command to launch the pipeline on-demand (sandbox default)."
  value       = "gcloud dataflow flex-template run ${local.job_name}-$(date +%s) --template-file-gcs-location=${var.template_spec_path} --region=${var.region} --parameters input_subscription=${var.input_subscription},output_table=${var.silver_transaction_table},dlq_topic=${var.dlq_topic}"
}
