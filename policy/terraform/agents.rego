# Agent identity posture (ADR-0023).
package finchat.terraform

import rego.v1

# Agent service accounts are named `finchat-<env>-agent-<sa_key>` by
# infra/modules/agent_registry, from the map that scripts/agents_catalog.py generates.
# The trailing hyphen matters: it distinguishes an agent identity from the shared
# runtime SA `finchat-<env>-agent`, which is a Cloud Run service account and carries
# no per-agent accountability of its own.
agent_sa_pattern := `^finchat-(dev|test|prod)-agent-`

# --- SA-1: an agent identity names its owner and its recertification date ----
# The registry gate (scripts/verify_agent_registry.py) asserts that the CATALOGUE is
# complete and that the code matches it. It cannot see a service account created by
# hand in Terraform, because such an account exists in no catalogue to be checked
# against — which is precisely how an agent reaches production unregistered.
#
# The agent_registry module writes owner and recertification date into the account
# description for exactly this reason. This rule checks that the description is
# there, so an agent SA added outside the module fails the plan rather than
# appearing, unattributed, in the audit trail six months later.
deny contains msg if {
	some rc in changed
	rc.type == "google_service_account"
	regex.match(agent_sa_pattern, rc.change.after.account_id)
	not accountable(rc.change.after)
	msg := sprintf(
		"SA-1: %s creates the agent identity %q without an owner and recertification date in its description. Register the agent in scripts/agents_catalog.py and let infra/modules/agent_registry create it (ADR-0023).",
		[rc.address, rc.change.after.account_id],
	)
}

accountable(after) if {
	contains(after.description, "owner ")
	contains(after.description, "recert due ")
}
