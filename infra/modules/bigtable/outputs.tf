output "instance_id" {
  description = "Bigtable instance id (set BIGTABLE_INSTANCE on the DaaS API to enable hot reads)."
  value       = google_bigtable_instance.hot.name
}
output "tables" {
  value = [google_bigtable_table.txn_by_account.name, google_bigtable_table.account_balance.name]
}
