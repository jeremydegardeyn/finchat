package finchat.terraform

import rego.v1

table(after) := {"resource_changes": [{
	"address": "module.agent_registry.google_bigquery_table.agent_registry",
	"type": "google_bigquery_table",
	"change": {"actions": ["create"], "after": after, "after_unknown": {}},
}]}

bucket(after, unknown_attrs) := {"resource_changes": [{
	"address": "module.foundation.google_storage_bucket.dataflow",
	"type": "google_storage_bucket",
	"change": {"actions": ["create"], "after": after, "after_unknown": unknown_attrs},
}]}

test_bq1_rejects_an_unprotected_prod_evidence_table if {
	count(deny) == 1 with input as table({"table_id": "agent_action_log", "dataset_id": "finchat_platform_prod", "deletion_protection": false})
}

test_bq1_accepts_a_protected_prod_evidence_table if {
	count(deny) == 0 with input as table({"table_id": "agent_action_log", "dataset_id": "finchat_platform_prod", "deletion_protection": true})
}

# Non-prod is where tables get torn down and rebuilt; protecting them there blocks
# the teardown the environment exists for.
test_bq1_ignores_nonprod if {
	count(deny) == 0 with input as table({"table_id": "agent_registry", "dataset_id": "finchat_platform_dev", "deletion_protection": false})
}

# The medallion tables are recomputable — bronze replays and the rest are views.
test_bq1_ignores_a_recomputable_table if {
	count(deny) == 0 with input as table({"table_id": "silver_transaction", "dataset_id": "finchat_silver_prod", "deletion_protection": false})
}

test_gcs1_rejects_acl_mode if {
	count(deny) == 1 with input as bucket({"name": "finchat-prod-bronze-raw", "uniform_bucket_level_access": false}, {})
}

test_gcs1_accepts_uniform_access if {
	count(deny) == 0 with input as bucket({"name": "finchat-prod-bronze-raw", "uniform_bucket_level_access": true}, {})
}
