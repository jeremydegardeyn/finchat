# infra/envs/enterprise — reference overlay (NOT applied)

This composes the full enterprise tier from [docs/11](../../docs/11-future-state-roadmap.md),
indexed in [/ENTERPRISE.md](../../../ENTERPRISE.md). It exists to **read and `plan`**, not to
deploy — the enterprise stack carries real standing cost (Spanner, GKE, Apigee, Composer, BQ
Editions, multi-region) that the sandbox deliberately avoids.

```bash
# Reference only. To make it real you'd bootstrap the state bucket + provider creds:
terraform init      # needs finchat-enterprise-tfstate bucket
terraform plan      # would show the full enterprise topology as a create plan
# terraform apply   # intentionally never run on this branch
```

Built in phases (see the build status checklist in /ENTERPRISE.md):

1. **Foundation** — `ent_network`, `ent_cmek`, `ent_org_policies`, `ent_vpc_sc` ✅ wired
2. Data · 3. Compute & orchestration · 4. API & edge · 5. AI · 6. Observability — appended per phase.

Validation here is `terraform fmt` only; `init/validate` needs providers + credentials and would
surface version/field drift, which is expected for an un-applied reference overlay.
