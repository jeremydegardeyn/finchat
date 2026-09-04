package finchat.terraform

import rego.v1

sa(after) := {"resource_changes": [{
	"address": "google_service_account.rogue",
	"type": "google_service_account",
	"change": {"actions": ["create"], "after": after, "after_unknown": {}},
}]}

test_sa1_rejects_an_agent_identity_with_no_accountability if {
	count(deny) == 1 with input as sa({"account_id": "finchat-prod-agent-shadow", "description": "temporary"})
}

test_sa1_rejects_an_agent_identity_with_no_description_at_all if {
	count(deny) == 1 with input as sa({"account_id": "finchat-prod-agent-shadow"})
}

# What infra/modules/agent_registry actually writes.
test_sa1_accepts_a_registered_agent_identity if {
	count(deny) == 0 with input as sa({
		"account_id": "finchat-prod-agent-banking-assi",
		"description": "Registered agent identity · owner transactions-product@datadinosaur.com · risk HIGH · recert due 2026-11-02",
	})
}

# The shared Cloud Run runtime SA is `finchat-<env>-agent` with no trailing hyphen.
# It is not an agent identity and carries no per-agent accountability.
test_sa1_ignores_the_shared_runtime_service_account if {
	count(deny) == 0 with input as sa({"account_id": "finchat-prod-agent", "description": "Conversational + loan agents (prod)"})
}

test_sa1_ignores_an_unrelated_service_account if {
	count(deny) == 0 with input as sa({"account_id": "finchat-prod-analyst-anon", "description": "Anonymous analyst tier (prod)"})
}
