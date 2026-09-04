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

# --- Scope: destroys are not policed ----------------------------------------
test_a_destroyed_resource_is_not_policed if {
	count(deny) == 0 with input as plan({
		"type": "google_project_iam_member",
		"change": {"actions": ["delete"], "after": null, "after_unknown": {}},
	})
}
