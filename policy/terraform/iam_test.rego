package finchat.terraform

import rego.v1

# A plan change, with sensible defaults, so each test states only what it is about.
plan(rc) := {"resource_changes": [object.union(
	{"address": "module.x.res", "change": {"actions": ["create"], "after": {}, "after_unknown": {}}},
	rc,
)]}

iam(type, after) := plan({"type": type, "change": {"actions": ["create"], "after": after, "after_unknown": {}}})

# --- IAM-1 -------------------------------------------------------------------
test_iam1_rejects_project_editor if {
	deny["IAM-1: module.x.res grants the basic role \"roles/editor\". Basic roles carry write and IAM-admin capability across the whole project; grant a predefined or custom role scoped to the task (see infra/modules/iam)."] with input as iam("google_project_iam_member", {"role": "roles/editor", "member": "serviceAccount:a@b"})
}

test_iam1_rejects_project_owner if {
	count(deny) == 1 with input as iam("google_project_iam_member", {"role": "roles/owner", "member": "serviceAccount:a@b"})
}

# The CI/CD deployer holds roles/viewer so `terraform plan` can refresh state. It is
# read-only and justified in modules/foundation; the gate must not fight it.
test_iam1_allows_viewer if {
	count(deny) == 0 with input as iam("google_project_iam_member", {"role": "roles/viewer", "member": "serviceAccount:cicd@b"})
}

test_iam1_allows_a_scoped_predefined_role if {
	count(deny) == 0 with input as iam("google_project_iam_member", {"role": "roles/bigquery.dataViewer", "member": "serviceAccount:a@b"})
}

# --- IAM-2 -------------------------------------------------------------------
test_iam2_rejects_public_bigquery if {
	count(deny) == 1 with input as iam("google_bigquery_dataset_iam_member", {"role": "roles/bigquery.dataViewer", "member": "allUsers"})
}

test_iam2_rejects_public_bucket if {
	count(deny) == 1 with input as iam("google_storage_bucket_iam_member", {"role": "roles/storage.objectViewer", "member": "allAuthenticatedUsers"})
}

test_iam2_allows_the_public_ui_service if {
	count(deny) == 0 with input as iam(
		"google_cloud_run_v2_service_iam_member",
		{"role": "roles/run.invoker", "member": "allUsers", "name": "finchat-prod-ui"},
	)
}

# The exception is the UI, not "any Cloud Run service in a module called ui".
test_iam2_rejects_a_public_backend_service if {
	count(deny) == 1 with input as iam(
		"google_cloud_run_v2_service_iam_member",
		{"role": "roles/run.invoker", "member": "allUsers", "name": "finchat-prod-txn-api"},
	)
}

# --- IAM-3 -------------------------------------------------------------------
test_iam3_rejects_an_authoritative_binding if {
	count(deny) == 1 with input as plan({
		"type": "google_project_iam_binding",
		"change": {"actions": ["create"], "after": {"role": "roles/run.invoker", "members": ["serviceAccount:a@b"]}, "after_unknown": {}},
	})
}

test_iam3_rejects_an_iam_policy if {
	count(deny) == 1 with input as plan({
		"type": "google_storage_bucket_iam_policy",
		"change": {"actions": ["create"], "after": {}, "after_unknown": {}},
	})
}

# --- Scope: the POSTURE rules do not police destroys -------------------------
# A resource being destroyed has no configuration left to judge, so IAM-1/2/3 must stay
# silent on it. The deletion itself is DESTROY-1's business, and that rule is expected to
# fire here — this asserts which rule speaks, not that nothing does.
test_a_destroyed_resource_is_judged_only_by_destroy1 if {
	msgs := deny with input as plan({
		"type": "google_project_iam_member",
		"change": {"actions": ["delete"], "after": null, "after_unknown": {}},
	})
	count(msgs) == 1
	some m in msgs
	startswith(m, "DESTROY-1")
}

# --- IAM-5 -------------------------------------------------------------------
# The grant that went unchallenged in September 2026, and the reason this rule exists:
# it passes IAM-1 because it is not called Owner or Editor.
test_iam5_rejects_iam_admin_on_any_principal if {
	count(deny) == 1 with input as iam(
		"google_project_iam_member",
		{"role": "roles/resourcemanager.projectIamAdmin", "member": "serviceAccount:agent@b"},
	)
}

test_iam5_rejects_role_admin if {
	count(deny) == 1 with input as iam("google_project_iam_member", {"role": "roles/iam.roleAdmin", "member": "serviceAccount:a@b"})
}

test_iam5_rejects_security_admin if {
	count(deny) == 1 with input as iam("google_project_iam_member", {"role": "roles/iam.securityAdmin", "member": "user:someone@b"})
}

# IAM-4 owns the deploy account. One grant should produce one finding, and it should be
# the one that names the identity split — not two rules shouting about the same line.
test_iam5_defers_to_iam4_on_the_deploy_account if {
	msgs := deny with input as iam(
		"google_project_iam_member",
		{"role": "roles/resourcemanager.projectIamAdmin", "member": "serviceAccount:finchat-dev-cicd@p.iam.gserviceaccount.com"},
	)
	count(msgs) == 1
	some m in msgs
	startswith(m, "IAM-4:")
}

# Impersonation is deliberately out of scope. Both are granted in this repo today, so
# denying them would ship the rule with a standing exception list — the failure IAM-1's
# own comment warns about.
test_iam5_allows_token_creator if {
	count(deny) == 0 with input as iam("google_project_iam_member", {"role": "roles/iam.serviceAccountTokenCreator", "member": "serviceAccount:a@b"})
}

test_iam5_allows_service_account_user if {
	count(deny) == 0 with input as iam("google_project_iam_member", {"role": "roles/iam.serviceAccountUser", "member": "serviceAccount:a@b"})
}

# Reading a policy is not changing one, which is why the deploy SA holds it.
test_iam5_allows_security_reviewer if {
	count(deny) == 0 with input as iam("google_project_iam_member", {"role": "roles/iam.securityReviewer", "member": "serviceAccount:a@b"})
}

# The way around a list of role names, closed.
test_iam5_rejects_a_custom_role_carrying_setiampolicy if {
	count(deny) == 1 with input as plan({
		"type": "google_project_iam_custom_role",
		"change": {"actions": ["create"], "after": {"permissions": ["bigquery.tables.get", "resourcemanager.projects.setIamPolicy"]}, "after_unknown": {}},
	})
}

# The three custom roles this repo actually defines must keep passing.
test_iam5_allows_a_scoped_custom_role if {
	count(deny) == 0 with input as plan({
		"type": "google_project_iam_custom_role",
		"change": {"actions": ["create"], "after": {"permissions": ["bigquery.tables.get", "bigquery.tables.getData"]}, "after_unknown": {}},
	})
}
