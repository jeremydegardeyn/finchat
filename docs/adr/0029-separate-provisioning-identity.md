# ADR-0029 — Terraform runs as a separate provisioning identity, not the deploy account

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Principal Cloud Architect
- **Context tags:** Least privilege, CI/CD identity, WIF, privilege escalation, separation of duties

## Context

`finchat-<env>-cicd@` was created by [`setup_wif.sh`](../../scripts/setup_wif.sh) for
**building and deploying**: Cloud Run, Artifact Registry, Dataflow, BigQuery jobs, GCS.
It is used by `build-deploy.yml`, which runs on **every push to `main`**.

`infra.yml` reused the same identity, and Terraform manages a far larger surface. Counted
from the source rather than from memory:

```
grep -rhoE '^resource "google_[a-z0-9_]+"' infra/ | sort | uniq -c
```

returns 55 distinct resource types across Pub/Sub, Secret Manager, Eventarc, Workflows,
Cloud Scheduler, Cloud SQL, Bigtable, DLP, Dataplex, Data Catalog, API Gateway, Model
Armor, Monitoring, Logging — and `google_project_iam_custom_role` and
`google_project_iam_member`. The deploy account held roles for essentially none of it.

The symptom was a queue of 403s. Each `terraform apply` got one resource further and
stopped: project IAM, then the billing budget, then a Pub/Sub subscription, with Secret
Manager, Eventarc, Data Catalog and Monitoring visibly waiting behind them. Each was
"fixed" by granting the deploy account another role.

That approach was heading somewhere specific and bad. The deploy account had already been
granted `roles/resourcemanager.projectIamAdmin` to unblock the first 403 — which means
**it could grant itself the rest**. A Terraform config adding a `google_project_iam_member`
for its own identity applies cleanly; the destroy guard added in the same period inspects
deletions, not privilege grants. The escalation path ran through the pipeline itself, and
the pipeline is triggered by a push to `main`.

Two further points sharpen it. The reference architecture's own policy-as-code
([ADR-0027](0027-policy-as-code.md)) denies `roles/owner` and `roles/editor` on the
rationale that basic roles *"carry write and IAM-admin capability across the whole
project"* — and `projectIamAdmin` grants precisely that IAM-admin capability while passing
the rule, because the rule matches role **names**, not capabilities. And `build-deploy.yml`
runs unattended on push, whereas `infra.yml` is manually dispatched behind the GitHub
Environment's required reviewers. Those are very different exposure profiles for one
identity to carry.

## Decision

**Split the identity by job, and let the trigger model decide who holds the privilege.**

| | `finchat-<env>-cicd@` | `finchat-<env>-provisioner@` |
|---|---|---|
| Used by | `build-deploy.yml`, `canary-eval.yml`, `live-eval.yml`, `reconcile-controls.yml` | `infra.yml` only |
| Trigger | push to `main`, unattended | manual dispatch, required reviewers on test/prod |
| Scope | build and deploy: Cloud Run, Artifact Registry, Dataflow, BigQuery jobs | owns the project's infrastructure: 24 roles, one per resource family Terraform declares |
| Can change project IAM | **no** (the grant is removed) | yes |

- **The role list is derived, not curated.** [`setup_provisioner.sh`](../../scripts/setup_provisioner.sh)
  carries one role per resource family in `infra/`, each with the resource type that
  requires it in a comment, so the set can be **re-derived** from the Terraform rather than
  accumulating by trial and error. That is the difference between a scoped account and one
  that grew a role every time a pipeline went red.
- **`infra.yml` requires `PROVISION_SA` and does not fall back to `DEPLOY_SA`.** A silent
  fallback would restore the exact coupling this removes, on the first environment where
  someone forgets to set a variable, and it would do it invisibly.
- **Bootstrapped outside Terraform.** It is the identity Terraform runs as, so Terraform
  cannot create it. Same constraint, same pattern as `setup_wif.sh`.
- **The billing grant stays a separate, opt-in step.** The budget lives on the billing
  account, which may fund projects beyond this one and is often org-administered, so
  `setup_provisioner.sh` skips it unless `BILLING_ACCOUNT` is exported.

## Consequences

- The identity that runs on every push can no longer rewrite project IAM, read or write
  secrets, or alter data policies. Privileged change requires a manual dispatch through an
  environment with required reviewers — separation of duties enforced by the trigger model
  rather than by convention.
- Two accounts and two GitHub variables per environment instead of one. Real cost, and the
  reason the READMEs and the bootstrap output both name it explicitly.
- **This is still a broad standing grant.** The provisioner holds 24 admin-class roles
  because Terraform genuinely manages that much. It is scoped by *use* — one workflow, one
  trigger, reviewer-gated — not by capability. The enterprise next step is short-lived
  impersonation with IAM Conditions, or a separate project per environment so the blast
  radius is bounded by the project rather than by the role list.
- **A gap this exposed and does not close:** IAM-1 matches role names. `projectIamAdmin`,
  `iam.roleAdmin` and `iam.serviceAccountAdmin` all pass a rule written to stop exactly the
  capability they confer. **Closed by two rules, deliberately split by what they reason
  about.** `IAM-4` (lifecycle.rego) is about *identity*: the deploy account may never be
  granted provisioning roles, so the split survives someone editing the workflow.
  `IAM-5` (iam.rego) is about *capability* regardless of identity: no principal this
  pipeline manages may receive a role that changes an IAM policy, and a custom role
  carrying a `setIamPolicy` permission is refused too — otherwise IAM-5 would reproduce
  IAM-1's name-matching weakness one level down. IAM-5 defers to IAM-4 on the deploy
  account so one grant yields one finding.

  What neither rule covers is impersonation. `serviceAccountTokenCreator` and
  `serviceAccountUser` are real escalation paths, bounded by the target's privileges and
  unavoidable under workload identity; both are granted in this repo today. Denying them
  would ship a rule with a standing exception list attached, which is the failure IAM-1's
  own comment warns about — so it is a stated gap rather than a rule nobody can satisfy.

## Alternatives considered

- **Keep granting the deploy account roles.** Simplest, and what was already happening.
  Rejected: it puts full project-administration on an identity that runs unattended on every
  push, and `projectIamAdmin` makes the remaining grants self-serviceable.
- **Give the deploy account `roles/owner`.** Ends the 403s immediately and is denied by the
  project's own IAM-1 rule. The rule is right.
- **Run Terraform locally from an operator's own credentials.** Genuinely least-privilege for
  CI, since there is no standing CI grant at all — but it deletes the audit trail, the policy
  gate and the destroy guard, and makes "who applied what" unanswerable. Wrong trade for a
  regulated reference architecture.
- **A project per environment.** The strongest containment, and the reason the consequence
  section names it as the enterprise step. Out of scope here: dev, test and prod deliberately
  share one project for cost ([docs/26](../26-controls-alerting.md) F18), which is also why
  IAM-3 forbids authoritative bindings.
