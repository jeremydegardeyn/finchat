# 08 — Cost Estimate

> List prices as of Jan 2026; verify against the [GCP Pricing Calculator](https://cloud.google.com/products/calculator).
> The design target is **near-zero idle cost**: when nobody is using the platform and no generation
> run is active, almost everything scales to zero.

## Sandbox monthly estimate (demo usage)

Assumes: a few generation runs/week (≤10k txns each), light API/agent traffic, scale-to-zero everywhere.

| Service | Pricing model | Free tier | Est. sandbox $/mo |
|---|---|---|---|
| BigQuery storage | $0.02/GiB active | 10 GiB free | **$0** (well under) |
| BigQuery queries | $6.25/TiB on-demand | 1 TiB/mo free | **$0** |
| Pub/Sub | $40/TiB throughput | 10 GiB free | **$0** |
| Cloud Storage | $0.02/GiB std | 5 GiB free | **~$0–1** |
| Dataflow (on-demand) | vCPU/mem/SE per hr | none | **~$2–8** (only while a run executes; drains after) |
| Cloud Run (APIs+UI) | per-100ms + reqs | 2M req, 360k GiB-s free | **$0** |
| API Gateway | per call | 2M calls/mo free | **$0** |
| Cloud Workflows | $0.01/1k steps | 5k steps/mo free | **$0** |
| Cloud Scheduler | $0.10/job/mo | 3 jobs free | **$0** |
| Agent Engine (scale-0) | vCPU-hr+GiB-hr | none | **~$0** idle |
| Gemini tokens | per 1M tokens | — | **~$1–5** (demo) |
| Cloud DLP | per unit inspected | small free | **~$0–2** (sampled) |
| Model Armor | per unit screened | — | **~$0–2** (chat volume) |
| Secret Manager | $0.06/secret/mo | 6 free | **$0** |
| Artifact Registry | $0.10/GiB | 0.5 GiB free | **~$0–1** |
| Cloud Build | per build-min | 2,500 min/mo free | **$0** |
| Logging/Monitoring | per GiB ingested | 50 GiB logs free | **$0** |
| **TOTAL** | | | **≈ $5–25/month** |

> The only meaningful variable cost is **Dataflow while a generation run is active**. Idle baseline ≈ **$0–3/mo**.

### Cost guardrails (deployed by Terraform)
- BigQuery **partition expiration** + table retention → storage never grows unbounded.
- Dataflow Flex Template **drains on completion**; `max_num_workers` capped; no Streaming Engine pin.
- Cloud Run / Agent Engine `min-instances = 0`.
- **Budget alert** at $10/$25/$50 via Cloud Billing Budgets (Pub/Sub + email).
- DLP **sampling** (e.g., inspect 10% in sandbox) instead of 100% scan.
- BigQuery **maximum bytes billed** per query set on API service accounts.

---

## Enterprise-scale cost shape (illustrative, NOT deployed)

At billions of txns / millions of customers the substituted services come into play:

| Service | Enterprise driver | Order-of-magnitude |
|---|---|---|
| BigQuery Editions (slots) | Reserved + autoscale slots for steady analytics | $$$ committed |
| Dataflow 24/7 streaming | Always-on autoscaled workers + Streaming Engine | $$$/mo per pipeline |
| **Apigee X** | Full API management + portal + analytics | $20k–100k+/yr |
| **Cloud Composer** | Batch DAG orchestration (recon, reg reporting, dbt) | $300–500/mo+ baseline |
| Agent Engine (warm) | Min-instance pool for latency SLOs | $80–130/mo+ per agent |
| Vertex/Gemini | Production token volume | usage-scaled |

The point of the [service mapping](07-service-selection-and-mapping.md) is that **moving from the
sandbox column to the enterprise column is configuration, not re-architecture.**
