# ADR-0007 — Cloud Run over GKE for service hosting

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** Platform engineering, cost engineering, API-first

## Context

DaaS APIs, the loan API, agents (fallback runtime), and the web UI need a container host. The brief
offers Cloud Run, Apigee, or GKE and asks us to justify the choice.

## Decision

Host all services on **Cloud Run**. Do **not** run GKE in the sandbox.

## Rationale

| Factor | Cloud Run | GKE |
|---|---|---|
| Idle cost | $0 (scale-to-zero) | Node pool always-on ($$) |
| Ops overhead | None (fully managed) | Cluster/node/upgrade mgmt |
| Free tier | 2M req + 360k GiB-s/mo | none |
| Fit | Stateless HTTP services & jobs | Complex stateful/multi-protocol workloads |
| Scale | Automatic to high concurrency | Manual/auto, more control |

For stateless, HTTP, scale-to-zero microservices, Cloud Run is strictly better on cost and ops with no
loss of capability. **Cloud Run Jobs** cover batch-style tasks (e.g., the generator).

## When GKE is warranted (enterprise trigger)

Workloads needing: non-HTTP protocols, sidecars/service mesh (Istio/ASM), GPU sharing, complex
stateful sets, or strict per-pod networking. At that point selected workloads move to GKE Autopilot;
the container images are unchanged.

## Consequences

- All components ship as containers in Artifact Registry, deployable to Cloud Run via CI/CD.
- Agents target Agent Engine first, Cloud Run as the portable fallback (see [0004](0004-agent-engine-vs-mcp.md)).
- Portability to GKE is preserved (same OCI images) should an enterprise trigger arise.
