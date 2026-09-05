# Shared plan-reading helpers for the Terraform posture rules.
#
# Every rule in this package reads `terraform show -json` output, so the shape of a
# "resource" here is a plan *change*, not a state object. Two consequences that the
# rules below all depend on:
#
#   * Only creates and updates are policed. A resource being destroyed cannot violate
#     a posture rule, and failing on one would block exactly the cleanups these rules
#     exist to encourage.
#   * An attribute that Terraform cannot resolve until apply is ABSENT from
#     `change.after` and named in `change.after_unknown`. A rule that treats unknown
#     as a violation denies a change that is actually fine, and the author has no way
#     to satisfy it. Where the distinction matters, the rule tests `after_unknown`
#     explicitly rather than inferring it from a missing key.
package finchat.terraform

import rego.v1

# Resources this plan will bring into existence or change.
changed contains rc if {
	some rc in input.resource_changes
	actions := {a | some a in rc.change.actions}
	actions & {"create", "update"} != set()
}

# True when Terraform cannot resolve `attr` on this resource until apply.
unknown(rc, attr) if {
	rc.change.after_unknown[attr]
}
