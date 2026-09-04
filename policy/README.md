# Policy

Rego rules evaluated by [conftest](https://www.conftest.dev/). Decision and rationale:
[ADR-0027](../docs/adr/0027-policy-as-code.md).

Two policy sets, two inputs, two places they run.

| Directory | Package | Input | Runs in |
|---|---|---|---|
| `terraform/` | `finchat.terraform` | `terraform show -json` | `.github/workflows/infra.yml`, between plan and apply |
| `registry/` | `finchat.registry` | `agents_catalog.py --emit-policy-input` | `.github/workflows/ci.yml`, the `policy` job |

## The rules

### Terraform posture

| Rule | Refuses |
|---|---|
| `IAM-1` | `roles/owner` or `roles/editor` on any binding |
| `IAM-2` | `allUsers` / `allAuthenticatedUsers`, except the demo UI's Cloud Run service |
| `IAM-3` | Authoritative `*_iam_binding` / `*_iam_policy` resources |
| `RUN-1` | A Cloud Run service without a usable `env` label |
| `BQ-1` | A production evidence table without deletion protection |
| `GCS-1` | A bucket with uniform bucket-level access disabled |
| `SA-1` | An agent service account with no owner and recertification date |

### Agent registry (ADR-0023)

| Rule | Refuses |
|---|---|
| `REG-1` | An agent missing owner, business area, risk tier, identity or data scope |
| `REG-2` | Two agents sharing a service account |
| `REG-3` | A consequential agent with no human-in-the-loop gate |
| `LIFE-1` | A recertification past its due date (warns inside 14 days) |
| `LIFE-2` | A due date that does not match the cadence the risk tier earns |

`DRIFT-1..4` are **not** here. They parse agent source with `ast` and stay in
[`scripts/verify_agent_registry.py`](../scripts/verify_agent_registry.py); the header
there explains the boundary.

## Running them locally

Install conftest — pin the same version the workflows use:

```bash
CONFTEST_VERSION=0.69.0
curl -sSfL "https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST_VERSION}/conftest_${CONFTEST_VERSION}_Linux_x86_64.tar.gz" | tar -xz conftest
sudo mv conftest /usr/local/bin/
```

The policy unit tests need nothing else:

```bash
conftest verify --policy policy/terraform
conftest verify --policy policy/registry
```

The registry rules against the committed registry:

```bash
python scripts/agents_catalog.py --env prod --emit-policy-input - \
  | conftest test --policy policy/registry --namespace finchat.registry --parser json -
```

The Terraform rules need a plan, so they need credentials for the target project:

```bash
cd infra/envs/dev
terraform init -input=false
terraform plan -input=false -out=tfplan
terraform show -json tfplan > tfplan.json
conftest test --policy ../../../policy/terraform --namespace finchat.terraform tfplan.json
```

## Writing a rule

Every rule carries a test that proves it fires and a test that proves it stays quiet on
the compliant shape. A rule nobody has seen fail is not a control, and the compliant case
is what stops the rule being tightened later into something that blocks ordinary work.

Two things the Terraform rules get wrong easily:

1. **Only creates and updates are policed.** A resource being destroyed cannot violate a
   posture rule, and failing on one blocks exactly the cleanups these rules encourage.
   `changed` in `terraform/lib.rego` handles this — iterate it, not
   `input.resource_changes`.
2. **Unknown-at-plan is not a violation.** An attribute Terraform cannot resolve until
   apply is absent from `change.after` and named in `change.after_unknown`. A rule that
   treats unknown as a violation fails builds for a reason nobody can act on, which is
   how a gate gets switched off. Use `unknown(rc, "attr")` where the distinction matters
   — and note that an attribute the author simply *omitted* is null in `after` and absent
   from `after_unknown`, which is a violation and must stay one.

## Scope

These rules stop a merge. They do not stop an API call: anyone with console access, or
any change that does not come through the pipeline, bypasses all of them. The enforcement
equivalents are Organization Policy constraints, IAM Conditions and BigQuery policy tags.
See ADR-0027 §"What this is not" — the distinction matters because a green check reported
as compliance ends the conversation about the gap.
