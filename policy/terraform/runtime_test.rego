package finchat.terraform

import rego.v1

service(after, unknown_attrs) := {"resource_changes": [{
	"address": "module.ui.google_cloud_run_v2_service.this",
	"type": "google_cloud_run_v2_service",
	"change": {"actions": ["create"], "after": after, "after_unknown": unknown_attrs},
}]}

test_run1_accepts_a_labelled_service if {
	count(deny) == 0 with input as service({"name": "finchat-prod-ui", "labels": {"env": "prod", "app": "finchat"}}, {})
}

test_run1_rejects_an_unlabelled_service if {
	count(deny) == 1 with input as service({"name": "finchat-prod-ui", "labels": {"app": "finchat"}}, {})
}

# An omitted `labels` block reads as null in the plan, not as an unknown value. That
# is the exact bug the rule is for, so it must fail rather than be skipped.
test_run1_rejects_a_service_with_no_labels_at_all if {
	count(deny) == 1 with input as service({"name": "finchat-prod-ui", "labels": null}, {})
}

test_run1_rejects_a_junk_env_label if {
	count(deny) == 1 with input as service({"name": "finchat-prod-ui", "labels": {"env": "production"}}, {})
}

# Unknown-at-plan is not a violation. Failing here would break builds for a reason
# nobody can act on, which is how a gate gets switched off.
test_run1_skips_labels_it_cannot_resolve_until_apply if {
	count(deny) == 0 with input as service({"name": "finchat-prod-ui"}, {"labels": true})
}
