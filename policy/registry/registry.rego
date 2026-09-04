# Agent registry policy (ADR-0023).
#
# These are the rules that used to live as hand-written Python inside
# verify_agent_registry.py. They moved here because of who has to read them: an agent
# inventory is a control the risk and audit functions rely on, and "every consequential
# agent declares a human-in-the-loop gate" should be legible to the people who own that
# requirement without them reading a Python for-loop first.
#
# The split with the Python gate is deliberate and not arbitrary:
#
#   * DRIFT-1..4 stay in Python. They parse the agent source files with `ast` and
#     compare constructors and tool lists against the catalogue. Rego over an abstract
#     syntax tree would be worse in every respect.
#   * REG-* and LIFE-* live here. They are assertions over a JSON document, which is
#     what Rego is for.
#
# Input document: `python scripts/agents_catalog.py --env <env> --emit-policy-input -`
package finchat.registry

import rego.v1

# --- REG-1: an agent nobody owns is an agent nobody can be asked about -------
required_fields := ["owner", "business_area", "risk_tier", "sa_key", "data_scope"]

deny contains msg if {
	some a in input.agents
	some field in required_fields
	not declared(a, field)
	msg := sprintf("REG-1: %s is missing %q — every agent declares an accountable owner, a supervising business area, a risk tier, its own identity, and the data it may reach.", [a.id, field])
}

declared(a, field) if {
	value := a[field]
	value != null
	value != ""
}

# --- REG-2: one identity per agent -------------------------------------------
# A shared service account makes the audit trail answer "one of these agents did it",
# which is not an answer. Reported once per pair rather than once per agent.
deny contains msg if {
	some a in input.agents
	some b in input.agents
	a.id < b.id
	a.sa_key == b.sa_key
	msg := sprintf("REG-2: %s and %s share the service account %q — every agent needs a distinct identity so its actions are individually attributable.", [a.id, b.id, a.sa_key])
}

# --- REG-3: consequential action requires a human gate ------------------------
deny contains msg if {
	some a in input.agents
	a.consequential == true
	object.get(a, "hitl", false) != true
	msg := sprintf("REG-3: %s takes consequential action but declares no human-in-the-loop gate.", [a.id])
}

# --- LIFE-1: recertification, on the privileged-human-access cycle -----------
deny contains msg if {
	some a in input.agents
	days_remaining(a) < 0
	msg := sprintf("LIFE-1: %s recertification overdue since %s (owner %s).", [a.id, a.recert_due, a.owner])
}

warn contains msg if {
	some a in input.agents
	remaining := days_remaining(a)
	remaining >= 0
	remaining <= 14
	msg := sprintf("LIFE-1: %s recertification due %s (%dd) — owner %s.", [a.id, a.recert_due, remaining, a.owner])
}

# --- LIFE-2: the due date matches the cadence the risk tier earns ------------
# `agents_catalog.py` computes recert_due from last_recertified and the tier cadence;
# this re-derives it and treats disagreement as a failure. That is deliberate
# duplication of the kind scripts/reconcile_controls.py already uses: two planes
# computed independently, and divergence is its own finding. It is what stops a due
# date being edited forward by hand to buy another quarter without recertifying.
recert_days := {"HIGH": 90, "MEDIUM": 180, "LOW": 365}

deny contains msg if {
	some a in input.agents
	expected := date_string(to_ns(a.last_recertified) + (recert_days[a.risk_tier] * day_ns))
	a.recert_due != expected
	msg := sprintf("LIFE-2: %s declares recert_due %s, but a %s-tier agent last recertified on %s is due %s.", [a.id, a.recert_due, a.risk_tier, a.last_recertified, expected])
}

# --- date helpers -------------------------------------------------------------
# `today` is supplied in the input rather than read from the clock, so a policy run
# is reproducible and a test can pin the date. The Python gate takes --today for the
# same reason.
day_ns := 86400000000000

to_ns(d) := time.parse_rfc3339_ns(sprintf("%sT00:00:00Z", [d]))

date_string(ns) := sprintf("%04d-%02d-%02d", time.date(ns))

days_remaining(a) := (to_ns(a.recert_due) - to_ns(input.today)) / day_ns
