package finchat.registry

import rego.v1

# A registered, compliant agent. Tests override only the field under test, so a new
# required field breaks the fixture once rather than in every case.
agent(overrides) := object.union(
	{
		"id": "a1",
		"owner": "loans-product@datadinosaur.com",
		"business_area": "Consumer Lending",
		"risk_tier": "HIGH",
		"sa_key": "agent-alpha",
		"data_scope": "The loan under review.",
		"consequential": false,
		"hitl": false,
		"last_recertified": "2026-08-04",
		"recert_due": "2026-11-02",
	},
	overrides,
)

registry(agents) := {"env": "test", "today": "2026-08-04", "agents": agents}

one(overrides) := registry([agent(overrides)])

# --- REG-1 -------------------------------------------------------------------
test_reg1_rejects_an_unowned_agent if {
	count(deny) == 1 with input as one({"owner": ""})
}

test_reg1_rejects_an_agent_with_no_data_scope if {
	count(deny) == 1 with input as one({"data_scope": null})
}

test_reg1_rejects_an_agent_missing_the_field_entirely if {
	count(deny) == 1 with input as registry([object.remove(agent({}), {"business_area"})])
}

test_reg1_accepts_a_complete_agent if {
	count(deny) == 0 with input as one({})
}

# --- REG-2 -------------------------------------------------------------------
test_reg2_rejects_a_shared_service_account if {
	count(deny) == 1 with input as registry([agent({"id": "a1"}), agent({"id": "a2"})])
}

test_reg2_reports_a_shared_identity_once_not_twice if {
	deny["REG-2: a1 and a2 share the service account \"agent-alpha\" — every agent needs a distinct identity so its actions are individually attributable."] with input as registry([agent({"id": "a1"}), agent({"id": "a2"})])
}

test_reg2_accepts_distinct_identities if {
	count(deny) == 0 with input as registry([agent({"id": "a1"}), agent({"id": "a2", "sa_key": "agent-beta"})])
}

# --- REG-3 -------------------------------------------------------------------
test_reg3_rejects_consequential_action_without_a_human_gate if {
	count(deny) == 1 with input as one({"consequential": true, "hitl": false})
}

# An absent hitl flag must fail the same way an explicit false does — otherwise the
# way to pass the gate is to delete the field.
test_reg3_rejects_a_missing_hitl_flag if {
	count(deny) == 1 with input as registry([object.remove(agent({"consequential": true}), {"hitl"})])
}

test_reg3_accepts_a_gated_consequential_agent if {
	count(deny) == 0 with input as one({"consequential": true, "hitl": true})
}

# --- LIFE-1 ------------------------------------------------------------------
test_life1_fails_an_overdue_recertification if {
	count(deny) == 1 with input as registry([agent({"last_recertified": "2026-01-16", "recert_due": "2026-04-16"})])
}

test_life1_warns_before_it_fails if {
	input_doc := registry([agent({"last_recertified": "2026-05-16", "recert_due": "2026-08-14"})])
	count(deny) == 0 with input as input_doc
	count(warn) == 1 with input as input_doc
}

test_life1_is_silent_well_before_the_due_date if {
	count(warn) == 0 with input as one({})
}

# --- LIFE-2 ------------------------------------------------------------------
test_life2_catches_a_due_date_edited_forward_by_hand if {
	count(deny) == 1 with input as one({"recert_due": "2027-11-02"})
}

test_life2_applies_the_cadence_the_tier_earns if {
	count(deny) == 0 with input as one({"risk_tier": "MEDIUM", "recert_due": "2027-01-31"})
}
