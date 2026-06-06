output "service_accounts" {
  value = module.foundation.service_account_emails
}
output "artifact_registry" {
  value = module.foundation.artifact_registry_repo
}
output "datasets" {
  value = {
    bronze = module.bigquery.bronze_dataset
    silver = module.bigquery.silver_dataset
    gold   = module.bigquery.gold_dataset
  }
}
output "ingest_topic" {
  value = module.pubsub.ingest_topic_name
}
output "dataflow_on_demand_hint" {
  value = module.dataflow.on_demand_launch_hint
}
output "service_urls" {
  value = {
    txn_api  = module.txn_api.uri
    loan_api = module.loan_api.uri
    agent    = module.agent.uri
    ui       = module.ui.uri
  }
}
output "api_gateway_url" {
  value = var.enable_api_gateway ? module.api_gateway[0].gateway_url : null
}
