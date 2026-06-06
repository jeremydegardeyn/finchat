output "ingest_topic" {
  value = google_pubsub_topic.ingest.id
}
output "ingest_topic_name" {
  value = google_pubsub_topic.ingest.name
}
output "dlq_topic" {
  value = google_pubsub_topic.dlq.id
}
output "dataflow_subscription" {
  value = google_pubsub_subscription.dataflow.id
}
