# ent_vertex_pt — Vertex AI Provisioned Throughput (documentation, no Terraform)

There is intentionally **no `.tf`** here: Vertex **Provisioned Throughput (PT)** is a
**capacity commitment measured in GSUs (Generative Scale Units)**, purchased as an order /
commitment through the console or your Google account team — it is not a first-class Terraform
resource. This module documents what the enterprise tier buys and configures around it.

## What PT gives you

- **Latency + availability SLOs** on Gemini calls under load, instead of best-effort on-demand
  capacity. Conversational Analytics (plan → SQL → execute → compose) and the agent path get
  predictable p95s during peak.
- A **dedicated throughput pool** so a traffic spike in one tenant doesn't starve another.

## How it's wired (no TF resource)

1. Order PT (GSUs) for the model + region via console / sales; it attaches to the project.
2. Requests automatically draw from the PT pool when `model` matches; overflow can spill to
   on-demand (configurable) so you never hard-fail.
3. Pair with **context caching** (`cachedContents` API) for the long, stable system instructions
   (graph join model, agent persona, analyst HARD-SCOPE rules) — cheaper and faster than
   resending them every call. That *is* API-configurable but lives in app code, not TF.

## Sandbox vs enterprise

| | Sandbox | Enterprise |
|---|---|---|
| Gemini capacity | on-demand (shared, variable latency) | **Provisioned Throughput** (committed GSUs, SLO) |
| Long system prompts | resent every call | **context caching** |
| Model tiering | Flash for everything | Flash-Lite (routing) / Flash (tools) / Pro (synthesis) |

Tracked as part of the enterprise overlay even though it produces no Terraform — flagging the
capacity decision is the point.
