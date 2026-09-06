package finchat.terraform

import rego.v1

destroy_plan(addresses) := {"resource_changes": [{
	"address": a,
	"type": "google_bigquery_table",
	"change": {"actions": ["delete"], "after": null, "after_unknown": {}},
} |
some a in addresses
]}

# A replacement is delete+create: the existing resource is destroyed whatever the plan
# summary calls it, which is why the rule reads actions rather than a summary line.
replace_plan := {"resource_changes": [{
	"address": "google_pubsub_topic.events",
	"type": "google_pubsub_topic",
	"change": {"actions": ["delete", "create"], "after": {"name": "x"}, "after_unknown": {}},
}]}

grant(member, role) := {"resource_changes": [{
	"address": "module.foundation.google_project_iam_member.sa_roles[\"cicd\"]",
	"type": "google_project_iam_member",
	"change": {"actions": ["create"], "after": {"member": member, "role": role}, "after_unknown": {}},
}]}

CICD := "serviceAccount:finchat-prod-cicd@p.iam.gserviceaccount.com"

# --- DESTROY-1 ----------------------------------------------------------------
test_destroy1_refuses_an_unacknowledged_destroy if {
	count(deny) == 1 with input as destroy_plan(["google_bigquery_table.agent_registry"])
}

test_destroy1_reports_every_doomed_resource_not_just_a_count if {
	msgs := deny with input as destroy_plan(["a.one", "a.two", "a.three"])
	count(msgs) == 3
}

test_destroy1_allows_an_acknowledged_destroy if {
	count(deny) == 0 with input as destroy_plan(["a.one"]) with data.allow_destroy as true
}

# GitHub hands a workflow_dispatch boolean to the shell as a string, and the workflow
# interpolates it, so the quoting is easy to get wrong in either direction.
test_destroy1_accepts_the_string_form_github_supplies if {
	count(deny) == 0 with input as destroy_plan(["a.one"]) with data.allow_destroy as "true"
}

test_destroy1_treats_an_explicit_false_as_unacknowledged if {
	count(deny) == 1 with input as destroy_plan(["a.one"]) with data.allow_destroy as false
}

# An acknowledged destroy is still listed, because the acknowledgement means somebody
# looked and a silent apply gives them nothing to look at.
test_destroy1_still_lists_an_acknowledged_destroy_as_a_warning if {
	count(warn) == 1 with input as destroy_plan(["a.one"]) with data.allow_destroy as true
}

test_destroy1_does_not_warn_when_it_is_already_denying if {
	count(warn) == 0 with input as destroy_plan(["a.one"])
}

test_destroy1_catches_a_replacement if {
	count(deny) == 1 with input as replace_plan
}

test_destroy1_is_silent_on_a_plan_that_destroys_nothing if {
	count(deny) == 0 with input as grant(CICD, "roles/run.developer")
}

# --- IAM-4 --------------------------------------------------------------------
test_iam4_refuses_iam_admin_on_the_deploy_account if {
	count(deny) == 1 with input as grant(CICD, "roles/resourcemanager.projectIamAdmin")
}

test_iam4_refuses_secret_admin_on_the_deploy_account if {
	count(deny) == 1 with input as grant(CICD, "roles/secretmanager.admin")
}

# The deploy SA holds this today to write the Dataflow Flex Template spec. A rule keyed
# on the word "admin" would fire on a grant that is correct, which is why the set is
# named roles rather than a pattern.
test_iam4_allows_the_object_admin_the_deploy_account_legitimately_holds if {
	count(deny) == 0 with input as grant(CICD, "roles/storage.objectAdmin")
}

test_iam4_allows_the_deploy_accounts_normal_roles if {
	every role in ["roles/run.developer", "roles/artifactregistry.writer",
		"roles/dataflow.developer", "roles/bigquery.jobUser", "roles/viewer"] {
		count(deny) == 0 with input as grant(CICD, role)
	}
}

# The provisioning account is supposed to hold these; the rule is about WHO, not WHICH.
test_iam4_does_not_fire_on_a_different_identity if {
	count(deny) == 0 with input as grant(
		"serviceAccount:finchat-prod-provisioner@p.iam.gserviceaccount.com",
		"roles/secretmanager.admin",
	)
}
