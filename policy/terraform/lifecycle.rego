# Destroys, and the identity split that keeps a deploy account from provisioning.
package finchat.terraform

import rego.v1

# --- DESTROY-1: a plan that removes resources has to be acknowledged -----------
# This rule is the one place that deliberately does NOT iterate `changed`. Every other
# rule polices what a resource will look like after apply, so a deletion has nothing to
# judge; this rule polices the deletion itself.
#
# The failure it exists for is recorded: run 33917709545 planned 22 destroys in dev
# because the gitignored tfvars were absent from the CI checkout and Terraform evaluated
# every variable at its default. Nothing was lost only because the deploy SA happened to
# lack delete permission. Supplying the variables removed that cause; this removes the
# class, so any future drift that makes Terraform believe a resource should go has to be
# acknowledged rather than discovered afterwards.
#
# Counts deletes rather than reading a summary, because a replacement is delete+create
# and destroys the existing resource whatever the summary calls it.
#
# `data.allow_destroy` comes from `conftest --data`, which merges the file's keys into the
# ROOT of `data` rather than namespacing them by filename. Both the JSON boolean and the
# string GitHub gives a workflow_dispatch boolean input are accepted, since the workflow
# interpolates the input and the quoting is easy to get wrong in either direction.
destroyed contains rc if {
	some rc in input.resource_changes
	"delete" in {a | some a in rc.change.actions}
}

destroy_acknowledged if data.allow_destroy == true

destroy_acknowledged if data.allow_destroy == "true"

deny contains msg if {
	some rc in destroyed
	not destroy_acknowledged
	msg := sprintf(
		"DESTROY-1: this plan destroys %s. If that is intended, re-run with allow_destroy=true. If it is not, the likeliest cause is missing or wrong variables — check the TFVARS secret for this environment before changing anything else.",
		[rc.address],
	)
}

# Acknowledged destroys are still listed. The point of the acknowledgement is that
# somebody looked, and a silent apply gives them nothing to look at.
warn contains msg if {
	some rc in destroyed
	destroy_acknowledged
	msg := sprintf("DESTROY-1: destroying %s (acknowledged via allow_destroy).", [rc.address])
}

# --- IAM-4: the deploy account does not get provisioning rights (ADR-0029) ----
# Terraform runs as the provisioning account and the deploy account builds and ships.
# The workflow enforces which identity it runs AS; this enforces what the deploy identity
# may ever be GRANTED, which is the half that survives someone editing the workflow.
#
# Named roles rather than a pattern on "admin": the deploy SA legitimately holds
# `roles/storage.objectAdmin` to write the Dataflow Flex Template spec, so a rule keyed
# on the word would fire on a grant that is correct today and teach people to work around
# it. This set is the capability the split exists to withhold — rewriting IAM, secrets,
# data policies and the resources Terraform owns.
deploy_sa_marker := "-cicd@"

provisioning_roles := {
	"roles/resourcemanager.projectIamAdmin",
	"roles/iam.serviceAccountAdmin",
	"roles/iam.serviceAccountKeyAdmin",
	"roles/iam.roleAdmin",
	"roles/secretmanager.admin",
	"roles/datacatalog.admin",
	"roles/bigquery.admin",
	"roles/pubsub.admin",
	"roles/cloudsql.admin",
	"roles/bigtable.admin",
	"roles/eventarc.admin",
	"roles/workflows.admin",
	"roles/dlp.admin",
	"roles/serviceusage.serviceUsageAdmin",
}

deny contains msg if {
	some rc in changed
	rc.type in iam_member_types
	member := rc.change.after.member
	contains(member, deploy_sa_marker)
	role := rc.change.after.role
	role in provisioning_roles
	msg := sprintf(
		"IAM-4: %s grants %q to the deploy account (%s). Terraform runs as the provisioning account precisely so the identity used on every push to main cannot rewrite the project's IAM, secrets and data policies (ADR-0029).",
		[rc.address, role, member],
	)
}
