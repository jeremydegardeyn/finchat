# Storage posture: what must survive, and what must not be reachable by ACL.
package finchat.terraform

import rego.v1

# --- BQ-1: the evidence store is deletion-protected in production ------------
# Scoped deliberately to the control-plane tables rather than to every table. The
# medallion tables are recomputable — bronze replays, silver and gold are views over
# it — so deletion protection there buys inconvenience, not safety, and a rule that
# fires on 20 recomputable tables is a rule that gets an exception file.
#
# These two are different in kind. `agent_registry` is what an examiner reads to
# learn what was running and who owned it; `agent_action_log` is the append-only
# attribution trail behind "who authorised this action". Neither can be recomputed
# from anything, so losing either destroys evidence rather than data (ADR-0023).
evidence_tables := {"agent_registry", "agent_action_log"}

deny contains msg if {
	some rc in changed
	rc.type == "google_bigquery_table"
	rc.change.after.table_id in evidence_tables
	endswith(rc.change.after.dataset_id, "_prod")
	rc.change.after.deletion_protection != true
	msg := sprintf(
		"BQ-1: %s is a production evidence table with deletion_protection disabled. It cannot be recomputed from any source; losing it destroys the audit trail, not a derived dataset (ADR-0023).",
		[rc.address],
	)
}

# --- GCS-1: uniform bucket-level access --------------------------------------
# With UBLA off, object ACLs remain live alongside IAM, so a bucket can be readable
# by a principal that appears in no IAM policy anywhere. That makes "who can read
# this" unanswerable from the bindings, which is the question an access review asks.
deny contains msg if {
	some rc in changed
	rc.type == "google_storage_bucket"
	not unknown(rc, "uniform_bucket_level_access")
	rc.change.after.uniform_bucket_level_access != true
	msg := sprintf(
		"GCS-1: %s has uniform_bucket_level_access disabled, so object ACLs can grant access that no IAM binding records.",
		[rc.address],
	)
}
