# Cloud Run posture: the labels the control plane depends on.
package finchat.terraform

import rego.v1

environments := {"dev", "test", "prod"}

# --- RUN-1: every service carries a trustworthy env label --------------------
# `ui/control_events.py` states the reason plainly: the `environment` field inside a
# control event is set by the emitting process and is therefore only as trustworthy
# as that process, while `resource.labels.env` on the log entry is stamped by Cloud
# Run from Terraform and the workload cannot forge it. The ServiceNow alert policy's
# labelExtractor reads the latter (ADR-0026).
#
# So a service deployed without this label does not fail loudly. It emits control
# events that route to the wrong assignment group, or to none — a governance control
# that reports success while delivering nothing. That is the failure this rule exists
# to make impossible, and it is worth a build break precisely because it is silent.
deny contains msg if {
	some rc in changed
	rc.type == "google_cloud_run_v2_service"
	not unknown(rc, "labels")
	not labelled_with_env(rc.change.after)
	msg := sprintf(
		"RUN-1: %s has no usable `env` label. Control-event routing reads resource.labels.env because the workload cannot forge it (ADR-0026); without it a control fires and nobody is paged. Pass `labels = local.labels`.",
		[rc.address],
	)
}

labelled_with_env(after) if {
	after.labels.env in environments
}
