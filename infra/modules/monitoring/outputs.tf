output "notification_channel" {
  value = var.notification_email == "" ? null : google_monitoring_notification_channel.email[0].id
}
output "audit_log_bucket" {
  value = google_logging_project_bucket_config.audit.bucket_id
}
