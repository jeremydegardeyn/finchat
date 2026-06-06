# Infrastructure (Terraform)

Infrastructure-as-Code for the FinChat platform. Reusable **modules** composed per **environment**.

## Layout

```
infra/
├── modules/
│   ├── foundation/   # APIs, per-workload SAs, project IAM, Artifact Registry, buckets, budget
│   ├── iam/          # custom least-privilege roles (DaaS reader, loan approver, pipeline op)
│   ├── bigquery/     # medallion datasets, policy-tag taxonomy (CLS), tables, Gold view, RLS
│   ├── pubsub/       # ingest topic + Avro schema, DLQ, BQ subscription, Dataflow subscription
│   ├── dlp/          # PII inspect + de-identify templates
│   ├── dataflow/     # on-demand Flex Template runner / 24-7 streaming toggle
│   ├── cloud_run/    # generic scale-to-zero service
│   ├── api_gateway/  # OpenAPI-driven DaaS front door (Apigee substitute)
│   ├── workflows/    # loan orchestration (Composer substitute)
│   └── monitoring/   # notification channel, DLQ alert, immutable audit log sink
└── envs/
    ├── dev/          # canonical composition (default project: strongsville-city-schools)
    ├── test/         # copy of dev wiring; env + state bucket differ
    └── prod/         # copy of dev wiring; enterprise toggles illustrated in tfvars
```

## Enterprise cost toggles (default OFF → near-zero cost)

| Variable | Off (sandbox) | On (enterprise) | Cost impact |
|---|---|---|---|
| `enable_streaming_job` | Dataflow on-demand, drains | 24/7 streaming job | $$$ |
| `run_min_instances` | Cloud Run scale-to-zero | warm min-instances | $$ |
| `enable_api_gateway` | — | API Gateway deployed | ~free |
| `enable_workflows` | — | loan workflow deployed | ~free |
| `enable_budget` | no budget | budget + alerts | free |

Promotion dev→prod is a **tfvars change**, not a code change (see [ADR-0002](../docs/adr/0002-serverless-substitution-strategy.md)).

## Usage

```bash
# One-time: create remote state buckets
./scripts/bootstrap_state.sh strongsville-city-schools us-central1

# Per environment
cd infra/envs/dev
cp terraform.tfvars.example terraform.tfvars   # edit as needed
terraform init
terraform plan
terraform apply
```

> **State:** GCS backend, one bucket per env (`finchat-<env>-tfstate`) so state never crosses
> environments. The committed `.terraform.lock.hcl` pins provider versions.

## Validation

```bash
terraform fmt -recursive
cd infra/envs/dev && terraform init -backend=false && terraform validate
```

Validated against Terraform 1.8.5 + hashicorp/google(-beta) 6.x.
