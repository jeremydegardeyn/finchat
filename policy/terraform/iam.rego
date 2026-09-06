# IAM posture. What may be granted, to whom, and by which kind of resource.
package finchat.terraform

import rego.v1

# Resource types that grant a role to a member. Additive (`_iam_member`) only —
# the authoritative forms are refused outright by IAM-3.
iam_member_types := {
	"google_project_iam_member",
	"google_folder_iam_member",
	"google_organization_iam_member",
	"google_service_account_iam_member",
	"google_storage_bucket_iam_member",
	"google_bigquery_dataset_iam_member",
	"google_bigquery_table_iam_member",
	"google_cloud_run_v2_service_iam_member",
	"google_pubsub_topic_iam_member",
	"google_pubsub_subscription_iam_member",
	"google_secret_manager_secret_iam_member",
	"google_workflows_workflow_iam_member",
}

# --- IAM-1: no basic roles ---------------------------------------------------
# `roles/viewer` is deliberately NOT on this list. The CI/CD deploy SA holds it so
# that `terraform plan` can refresh state across every module, and that grant is
# read-only, justified in modules/foundation/main.tf, and reviewed. Including it
# would mean shipping this rule with a standing exception attached, so the rule is
# scoped to the two roles that have no such argument: Owner and Editor both carry
# write and IAM-admin capability across the whole project.
basic_roles := {"roles/owner", "roles/editor"}

deny contains msg if {
	some rc in changed
	rc.type in iam_member_types
	role := rc.change.after.role
	role in basic_roles
	msg := sprintf(
		"IAM-1: %s grants the basic role %q. Basic roles carry write and IAM-admin capability across the whole project; grant a predefined or custom role scoped to the task (see infra/modules/iam).",
		[rc.address, role],
	)
}

# --- IAM-2: nothing is public except the surfaces that are meant to be -------
public_members := {"allUsers", "allAuthenticatedUsers"}

deny contains msg if {
	some rc in changed
	rc.type in iam_member_types
	member := rc.change.after.member
	member in public_members
	not sanctioned_public(rc)
	msg := sprintf(
		"IAM-2: %s grants %q to %s. Public access is sanctioned only for the demo UI's Cloud Run service; every other surface authenticates (ADR-0006, ADR-0019).",
		[rc.address, rc.change.after.role, member],
	)
}

# The one public surface: the demo UI, which serves the sign-in page itself and so
# cannot sit behind run.invoker. Matched on the service NAME rather than the module
# address, so renaming or copying the module does not silently inherit the exception.
sanctioned_public(rc) if {
	rc.type == "google_cloud_run_v2_service_iam_member"
	rc.change.after.role == "roles/run.invoker"
	regex.match(`^finchat-(dev|test|prod)-ui$`, rc.change.after.name)
}

# --- IAM-3: additive bindings only -------------------------------------------
# `*_iam_binding` and `*_iam_policy` are AUTHORITATIVE: on apply they remove every
# member of that role they do not themselves list. FinChat's dev, test and prod
# share one project (docs/26 F18), so an authoritative binding written by one
# environment's state silently strips the bindings another environment applied,
# and the damage shows up as an unrelated permission error days later.
deny contains msg if {
	some rc in changed
	authoritative(rc.type)
	msg := sprintf(
		"IAM-3: %s is an authoritative IAM resource (%s). It removes bindings it does not list, and dev/test/prod share one project — use the additive `_iam_member` form.",
		[rc.address, rc.type],
	)
}

authoritative(t) if endswith(t, "_iam_binding")

authoritative(t) if endswith(t, "_iam_policy")

# --- IAM-5: nobody gets the power to grant -----------------------------------
# IAM-4 (lifecycle.rego) withholds provisioning rights from the deploy account by
# identity. This rule is about capability regardless of identity: no principal this
# pipeline manages gets a role that can change an IAM policy, because such a role makes
# every other restriction here self-serviceable.
#
# IAM-1 matches role NAMES, and that is the hole. `roles/resourcemanager.projectIamAdmin`
# confers precisely the IAM-admin capability IAM-1's own rationale names, and passes it
# untouched because it is not called Owner or Editor. It was granted to the deploy account
# in September 2026 to clear a 403 and no control objected.
#
# The line is the ability to CHANGE an IAM policy, not to act as another identity.
# `serviceAccountTokenCreator` and `serviceAccountUser` are impersonation: real escalation
# vectors, but bounded by the target's own privileges, unavoidable under workload identity,
# and already granted here. Including them would ship this rule with a standing exception
# list attached, which is the failure IAM-1's comment warns about. `iam.securityReviewer`
# is read-only and stays allowed for the reason it is granted: reading a policy is not
# changing one.
policy_mutating_roles := {
	"roles/resourcemanager.projectIamAdmin",
	"roles/resourcemanager.folderIamAdmin",
	"roles/resourcemanager.organizationAdmin",
	"roles/iam.securityAdmin",
	"roles/iam.roleAdmin",
	"roles/iam.organizationRoleAdmin",
	"roles/iam.serviceAccountAdmin",
	"roles/iam.workloadIdentityPoolAdmin",
}

deny contains msg if {
	some rc in changed
	rc.type in iam_member_types
	role := rc.change.after.role
	role in policy_mutating_roles

	# The deploy account is IAM-4's case. Deferring keeps one finding per grant and the
	# more specific message, rather than two rules shouting about the same line.
	not contains(rc.change.after.member, deploy_sa_marker)
	msg := sprintf(
		"IAM-5: %s grants %q, which lets the grantee change IAM policy and therefore grant itself anything else. Privileged provisioning identities are bootstrapped outside Terraform (scripts/setup_provisioner.sh, ADR-0029) so this pipeline cannot escalate itself.",
		[rc.address, role],
	)
}

# The obvious way around any list of role names: define a custom role carrying a
# setIamPolicy permission and grant that instead. A name-based check that is sidestepped
# by choosing a different name is the weakness IAM-5 exists to fix, so it would be a poor
# rule that reproduced it one level down.
deny contains msg if {
	some rc in changed
	rc.type == "google_project_iam_custom_role"
	some permission in rc.change.after.permissions
	endswith(lower(permission), ".setiampolicy")
	msg := sprintf(
		"IAM-5: %s defines a custom role including %q. setIamPolicy is the power to grant, whatever the role is called.",
		[rc.address, permission],
	)
}
