# FinChat — Enterprise Build (reference overlay)

> **Status: reference IaC, NOT deployed.** This branch (`enterprise-build`) expresses the
> full enterprise tier from [docs/11 — Future-State Roadmap](docs/11-future-state-roadmap.md)
> as Terraform/config, so you can read and `plan` the target architecture. It is deliberately
> **never applied** — the enterprise tier carries real, ongoing cost (Spanner, GKE, Apigee,
> BQ Editions, Composer, multi-region) that the sandbox exists specifically to avoid. Treat the
> modules here as a coherent, fmt-clean blueprint, not battle-tested production code.

The sandbox keeps the *contracts* (medallion layering, OpenAPI DaaS, event backbone, agent
patterns, IaC, governance taxonomy); this overlay swaps the *implementations* behind them and
raises capacity + assurance. Everything composes in `infra/envs/enterprise/`.

## Sandbox → enterprise mapping

| # | Concern | Sandbox (main) | Enterprise (this branch) | Module |
|---|---|---|---|---|
| 1 | Network | default network | VPC + private subnets, Private Google Access, Cloud NAT, PSC | `modules/ent_network` |
| 2 | Data-plane isolation | project IAM only | **VPC Service Controls** perimeter | `modules/ent_vpc_sc` |
| 3 | Encryption | Google-managed keys | **CMEK** (Cloud KMS) on BQ/GCS/Spanner/Bigtable/Pub/Sub | `modules/ent_cmek` |
| 4 | Org guardrails | none | **Org policies** (domain-restricted sharing, no extkeys, etc.) | `modules/ent_org_policies` |
| 5 | Analytical warehouse | BigQuery on-demand | **BQ Editions reservations** + BI Engine | `modules/ent_bq_reservation` |
| 6 | Hot serving | Bigtable emulator (toggle off) | **Bigtable** replicated multi-cluster + autoscaling + vector index | `modules/ent_bigtable` |
| 7 | Ledger / system of record | (none — BQ stands in) | **Spanner** payments ledger (interleaved, change streams) | `modules/ent_spanner` |
| 8 | Operational OLTP | (loan tables in BQ) | **AlloyDB** (Postgres + pgvector) for loan origination | `modules/ent_alloydb` |
| 9 | Services compute | Cloud Run (scale-to-zero) | **GKE Autopilot** + Workload Identity + HPA | `modules/ent_gke` |
| 10 | Orchestration | Cloud Workflows | **Cloud Composer 2** (Airflow) | `modules/ent_composer` |
| 11 | API management | API Gateway | **Apigee X** org/instance + proxies + products + portal | `modules/ent_apigee` |
| 12 | Edge / ingress | BFF serves SPA | **Global HTTPS LB + Cloud CDN + Cloud Armor** | `modules/ent_edge` |
| 13 | Workforce identity | GIS + BFF RBAC | **IAP** on staff surface + workforce IdP federation | `modules/ent_iap` |
| 14 | Customer identity | unauthenticated | **Identity Platform** (CIAM) | `modules/ent_identity_platform` |
| 15 | Vector search | BQ `VECTOR_SEARCH` | **Vertex AI Vector Search** (ANN at scale) | `modules/ent_vector_search` |
| 16 | LLM latency/SLO | on-demand Gemini | **Vertex Provisioned Throughput** + context caching | `modules/ent_vertex_pt` |
| 17 | Streaming | on-demand Flex Template | **Streaming Engine** 24/7 + Storage Write API | (env wiring + `enable_streaming_job`) |
| 18 | Serving rollups | logical views | **Materialized views + scheduled refresh** | `modules/ent_materialized_views` |
| 19 | Observability | eval card + logs | **SLOs, dashboards, alerting, log sinks → BQ** | `modules/ent_observability` |
| 20 | Topology | single project | **multi-project data mesh** (host + per-domain) | documented in §Data mesh |

## Phased build order (how this branch was assembled)

1. **Foundation** — network, CMEK, org policies, VPC-SC.  ← start here
2. **Data** — BQ reservations, Bigtable, Spanner, AlloyDB, materialized views.
3. **Compute & orchestration** — GKE, Composer.
4. **API & edge** — Apigee, global LB/CDN/Armor, IAP, Identity Platform.
5. **AI** — Vertex Vector Search, Provisioned Throughput.
6. **Observability** — SLOs, dashboards, log sinks.

## Build status

- [x] `ENTERPRISE.md` (this index) + `infra/envs/enterprise/` composition
- [x] Phase 1 — Foundation (network / CMEK / org policies / VPC-SC)
- [x] Phase 2 — Data (BQ reservation / Bigtable / Spanner / AlloyDB / matviews)
- [x] Phase 3 — Compute & orchestration (GKE Autopilot + manifests / Composer + DAG)
- [x] Phase 4 — API & edge (Apigee / edge LB+CDN+Armor / IAP / Identity Platform)
- [x] Phase 5 — AI (Vector Search; Provisioned Throughput documented)
- [x] Phase 6 — Observability (audit sink / dashboard / SLO + fast-burn alert / uptime)

All modules `terraform fmt` clean. Not `init`/`validate`'d (no providers/creds) and never applied — reference overlay, as designed.

## Data mesh (topology note)

At enterprise scale the single sandbox project splits into a **host project** (shared VPC,
VPC-SC perimeter, CMEK, Apigee, observability) and **per-domain data-product projects**
(transactions, loans, customer-360), each owning its own BigQuery datasets, service accounts,
and Dataplex registration, federated into one **Dataplex lake** for the catalog/governance
plane. This branch models a single enterprise project for readability; the multi-project split
is the same modules parameterized per project + a `google_folder`/`google_project` factory.

## Caveats (read before judging the code)

- **Not applied, not validated against live providers.** `terraform fmt` passes; `terraform
  init/validate` needs provider creds and would surface version/field drift.
- Some resources are **representative** (e.g. one Apigee proxy, one GKE workload) to show the
  pattern rather than enumerate every service.
- Costs are why this stays a branch: Spanner (~$650+/mo min), GKE, Apigee X, Composer, and BQ
  Editions each carry standing charges. The sandbox's whole point is to encode this architecture
  at ~$0 idle and only describe the enterprise bill, not pay it.
