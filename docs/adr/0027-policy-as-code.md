# ADR-0027 — Policy-as-code with OPA/Rego for infrastructure posture and the agent registry

- **Status:** Proposed
- **Date:** 2026-09-04
- **Deciders:** Principal Data Architect
- **Context tags:** governance, policy-as-code, CI/CD, IaC, agent registry, audit

## Context

FinChat already enforces rules mechanically in several places. `verify_agent_registry.py`
fails the build when an agent's tools drift from the registry; `compile_okf.py` turns the
refusal playbook into agent instructions with a drift test behind it; the AI gateway
refuses an unregistered workload class at runtime. None of that is written as policy — it
is hand-rolled Python and deployment config that happens to enforce policy.

There was one place with no mechanical check at all: **Terraform.** The whole infra gate
was `terraform fmt -check` and `terraform validate`. Both answer "is this syntactically
valid HCL", neither answers "is this an acceptable change". A pull request could add a
project-level `roles/editor` binding, publish a backend service to `allUsers`, drop the
`env` label that control-event routing depends on, or create an agent service account
that exists in no registry — and every check would stay green.

The roadmap (docs/11) already named the target as "central policy-as-code (Org Policy +
OPA)". This ADR takes the first increment of it.

Three constraints shaped the scope:

| | Constraint |
|---|---|
| C1 | **There is no Kubernetes.** ADR-0007 chose Cloud Run. Gatekeeper and Kyverno are admission controllers for an API server FinChat does not run, so most of the standard OPA playbook does not apply here. |
| C2 | **Terraform does not own the runtime env vars.** `infra/modules/cloud_run` sets `ignore_changes` on `template[0].containers[0].env` because CI/CD deploys them with `gcloud run deploy --set-env-vars`. A plan-time rule asserting `AI_GATEWAY_REQUIRED` would be asserting something the plan cannot see. |
| C3 | **Dev, test and prod share one project** (docs/26 F18), so environment is a resource label rather than a boundary — which makes authoritative IAM resources actively dangerous and the `env` label load-bearing. |

## Decision

**1. Conftest over `terraform show -json`, between plan and apply.**

Seven rules in `policy/terraform/`, evaluated in `.github/workflows/infra.yml`:

| Rule | What it refuses |
|---|---|
| IAM-1 | `roles/owner` or `roles/editor` on any binding |
| IAM-2 | `allUsers` / `allAuthenticatedUsers`, except the demo UI's Cloud Run service |
| IAM-3 | Authoritative `*_iam_binding` / `*_iam_policy` resources (C3) |
| RUN-1 | A Cloud Run service with no usable `env` label |
| BQ-1 | A production evidence table without deletion protection |
| GCS-1 | A bucket with uniform bucket-level access disabled |
| SA-1 | An agent service account with no owner and recertification date |

`roles/viewer` is deliberately absent from IAM-1. The CI/CD deploy SA holds it so
`terraform plan` can refresh state across every module; the grant is read-only and
justified in `infra/modules/foundation/main.tf`. A rule with one permanent exception
teaches people that exceptions are how you pass the gate.

The plan is written to a file and **apply applies that exact plan**, rather than
re-planning. Re-planning at apply time would leave a window in which what was inspected
and what was applied differ, and the gate would be checking a plan nobody ran.

**2. Rego for the agent registry's completeness and lifecycle rules.**

REG-1/2/3 and LIFE-1 move out of `verify_agent_registry.py` into `policy/registry/`.
LIFE-2 is new and only became natural once the rules were declarative: it re-derives each
recertification due date from `last_recertified` and the tier cadence, and fails on
disagreement with the catalogue — which is what catches a due date edited forward by hand
to buy another quarter. That is the same shape as `scripts/reconcile_controls.py`: two
planes computed independently, divergence is its own finding.

**3. The split between Python and Rego follows what each check has to read.**

DRIFT-1..4 stay in Python because they parse agent source files with `ast` and compare
constructors, tool lists and model arguments against the catalogue. Rego over a syntax
tree would be worse in every respect. What moved is the set of assertions over a JSON
document — and, not incidentally, the set the risk function has to be able to read.
"Every consequential agent declares a human-in-the-loop gate" should be legible without
reading a Python for-loop first.

**4. The rules are tested like the code they gate.**

`conftest verify` runs the policy unit tests in CI: for each rule, a case proving it
fires on the violation it names and a case proving it stays quiet on the compliant shape.
A rule nobody has seen fail is not a control.

**5. Conftest is version-pinned.**

An unpinned policy engine changes the rules' meaning on someone else's release schedule.

## What this is not

**These rules are a pre-check, not a control.** They run in CI, so they stop a merge —
they do not stop an API call. Anyone with console access, or any change that does not
come through this pipeline, bypasses all of it. The platform-level equivalents are the
actual enforcement: Organization Policy constraints, IAM Conditions, and the policy tags
already applied in `infra/modules/bigquery`.

This is the same distinction docs/23 draws about the AI gateway between traffic that is
*counted* and traffic that is *enforced*, and it is worth stating here for the same
reason: a green check reported as compliance is worse than no check, because it ends the
conversation about the gap.

The honest sequencing is that policy-as-code in CI is what you build **while** the org
policies are being negotiated, and it becomes redundant for any rule an Org Policy
constraint later covers.

## Alternatives considered

- **Gatekeeper / Kyverno.** Kubernetes admission controllers. No cluster (C1).
- **Sentinel.** Terraform-native and better integrated, but it is a Terraform Cloud /
  Enterprise feature and FinChat runs the open-source CLI in GitHub Actions.
- **Keeping the checks in Python.** The status quo, and it works. Rejected for the
  registry rules on audience: an agent inventory is a control the risk function relies
  on, and the rules should be reviewable by the people who own that requirement.
  Explicitly *not* rejected for the drift checks, which stay in Python.
- **OPA as the AI gateway's runtime PDP.** The highest-value use of a policy engine here:
  it would version the gateway's authorization rules separately from the service and let
  every audit row carry the `policy_version` that decided it — today the audit event
  records the outcome but not the rule that produced it. Deferred, not rejected. It needs
  the policy embedded (a bundle evaluated in-process) rather than reached over a network,
  because the gateway deliberately fails *open* to Vertex on transport failure while a
  policy refusal must never fall back (ADR-0024), and an unreachable network PDP is
  indistinguishable from a deny.
- **A broader deletion-protection rule over every BigQuery table.** Rejected: the
  medallion tables are recomputable — bronze replays, silver and gold are views over it —
  so the rule would fire on twenty tables that do not need it and earn itself an
  exception file. Scoped to the two control-plane tables that cannot be recomputed.

## Consequences

- A new posture rule is a Rego file and two tests, not a Python function nobody outside
  the team reads.
- Two languages in the registry gate. Accepted; the boundary is documented in the
  `verify_agent_registry.py` header and above.
- `terraform apply` now applies a saved plan. Anyone running apply by hand outside CI
  gets no gate — see "What this is not".
- The `env`-label dependency of control-event routing (ADR-0026) is enforced at plan time
  rather than only described in `ui/control_events.py`.

## References

- `policy/README.md` — the rules, and how to run them locally
- ADR-0023 (agent registry), ADR-0024 (AI gateway), ADR-0026 (controls alerting)
- docs/11 §Org-wide mesh — the enterprise target this is the first increment of
