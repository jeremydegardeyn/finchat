# ADR-0010 — Run the agents on Cloud Run (scale-to-zero) rather than Agent Engine

- **Status:** Accepted
- **Date:** 2026-06-07
- **Deciders:** Principal Data Architect
- **Context tags:** Agentic AI, cost engineering, platform

## Context

[ADR-0004](0004-agent-engine-vs-mcp.md) chose ADK + Vertex AI **Agent Engine** as the preferred
runtime (managed sessions, tracing, eval). In practice, a deployed Agent Engine reasoning engine
**bills a per-engine compute baseline (~$75–110/mo each) and does not cleanly scale to zero**.
Multiplied across agents × environments that becomes the single largest cost on a platform whose whole
premise is near-zero idle. (A first Agent Engine deploy also failed to start in the sandbox.)

## Decision

Deploy the ADK agents on **Cloud Run** (the portable fallback noted in ADR-0004): a small FastAPI
wrapper runs the ADK `Runner` behind `POST /chat`. Cloud Run **scales to zero** (~$0 idle), only
billing while handling a request. Keep `deploy.py` so the *same agent* can still be pushed to Agent
Engine when its managed sessions/eval/warm-pool are worth the baseline.

## Rationale

- **Near-zero cost** — the platform's core constraint; Cloud Run idles at $0.
- **Same code** — identical ADK agent; only the host differs. No lock-in.
- **Consistent platform** — agents become normal Cloud Run services: per-workload SA, private ingress,
  OIDC service-to-service auth, Cloud Logging/Monitoring, CI/CD — same as every other service.
- **Security** — agent deploys **private**; the UI BFF calls it with a minted **OIDC id-token** (the
  BFF SA holds `run.invoker`). Model Armor still screens prompt/response at the BFF.

## Consequences

- Agent runs as `finchat-<env>-agent` (Cloud Run); Gemini via Vertex (`GOOGLE_GENAI_USE_VERTEXAI`,
  runtime SA has `aiplatform.user`). Built + deployed by CI/CD.
- Sessions are in-memory (reset on cold start) — fine for the sandbox; use `VertexAiSessionService` /
  a datastore for durable multi-turn at scale.
- Evaluation runs via the offline harness ([eval/](../../eval/)) instead of the Agent Engine eval
  service; Vertex Gen AI Eval remains available for live scoring.
- Cold-start latency (a few seconds + model time) is accepted; an enterprise toggle (min-instances or
  Agent Engine warm pool) removes it.
