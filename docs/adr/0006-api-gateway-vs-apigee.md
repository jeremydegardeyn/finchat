# ADR-0006 — Cloud API Gateway over Apigee X for the sandbox DaaS layer

- **Status:** Accepted
- **Date:** 2026-06-06
- **Deciders:** Principal Data Architect
- **Context tags:** API-first, Data-as-a-Service, cost engineering

## Context

The Data-as-a-Service layer (Get Balance, Transaction History, Recent Activity, Account Summary) needs
an API-management front door: routing, auth, API keys, quotas, OpenAPI contracts. Apigee X is the
enterprise-preferred option but has no scale-to-zero / free tier (~$350–700+/mo PAYG idle).

## Decision

Use **Cloud API Gateway** (or Cloud Endpoints/ESPv2) in front of **Cloud Run** services for the
sandbox. Author **OpenAPI 3 specs first**; API Gateway is configured directly from them.

## Rationale

- **Scale-to-zero, ~free:** 2M calls/mo free, then ~$3/M; no idle cost.
- **API-first preserved:** the OpenAPI specs are the source of truth and **import directly into Apigee**
  as API proxies — migration is re-hosting the contract, not rewriting it.
- **Sufficient capability:** key/JWT auth, quotas, routing — everything DaaS needs to prove the pattern.

## What we consciously defer to Apigee (enterprise triggers)

API monetization, advanced mediation/transformation policies, a developer portal, hybrid/multi-cloud
gateways, and deep API analytics. None are required to demonstrate DaaS; all are added by re-hosting
the same OpenAPI contracts in Apigee when the business needs them.

## Consequences

- DaaS APIs are contract-first; specs live alongside the services and drive both Gateway config and
  client/agent tool definitions.
- Security is enforced at the gateway (keys/JWT) **and** at Cloud Run (IAM, service-to-service auth).
