# 16 — Contract Catalog: API, Data & Event Contracts

FinChat is contract-first. Every boundary between a producer and a consumer is governed
by an explicit, versioned contract that is **code** (reviewed in PRs) and **enforced** at
runtime — not a wiki page that drifts. There are three contract surfaces:

| Surface | Governs | Artifact (code) | Enforced by | Versioning |
|---|---|---|---|---|
| **API contract** | request/response of the serving + write APIs | OpenAPI + FastAPI `response_model` | API Gateway (schema + API key) · FastAPI validation · OIDC | URI `/v1` + OpenAPI `info.version` |
| **Data contract** | the shape & guarantees of a data product | [`contracts/*.yaml`](../contracts/) + `data-contract` catalog aspect | BigQuery policy tags (CLS) · Dataplex datascans (DQ) · partitioning | semver in the YAML (`version:`) |
| **Event contract** | messages on the ingestion bus | [`transaction_message.schema.json`](../products/transactions/schemas/transaction_message.schema.json) + Pub/Sub Avro schema | Pub/Sub topic `schema_settings` (reject at publish) · DLQ | versioned schema **name** (`…-transaction-v2`) |

```mermaid
flowchart LR
  PUB[Producers / generator] -->|EVENT contract<br/>Pub/Sub Avro schema| PS[(Pub/Sub ingest)]
  PS --> DF[Dataflow] --> BQ[(BigQuery medallion)]
  BQ -->|DATA contract<br/>contracts/*.yaml + aspects| DP[Data products]
  DP -->|API contract<br/>OpenAPI / FastAPI| GW[API Gateway / Cloud Run] --> CONS[Apps · agents · integrations]
  classDef c fill:#1e293b,stroke:#38bdf8,color:#e2e8f0; class PS,DF,BQ,DP,GW c;
```

---

## 1. API contracts

The DaaS and write APIs publish an OpenAPI contract; consumers (apps, the ADK agent's
tools, partner integrations) bind to it, never to internal tables.

- **Transactions DaaS** (read) — `products/transactions/api/`, OpenAPI at runtime
  (`/openapi.json`) and the Gateway spec [`openapi.gateway.yaml`](../products/transactions/api/openapi.gateway.yaml)
  (the *same* contract imports into Apigee X for the enterprise path, ADR-0006):
  | Method · Path | Purpose | Response model |
  |---|---|---|
  | `GET /v1/accounts/{id}/balance` | current balance | `Balance` |
  | `GET /v1/accounts/{id}/transactions` | recent transactions | `Transaction[]` |
  | `GET /v1/accounts/{id}/summary` | activity rollup | `AccountSummary` |
  | `GET /v1/accounts/samples` | sample account ids (demo) | `string[]` |
  | `GET /healthz` | liveness | — |
- **Loan API** (read + write) — `products/loans/api/`: `POST /v1/loans` (submit),
  `GET /v1/loans/{id}` (status), `GET /v1/loans` (review queue, employee),
  `GET /v1/loans/{id}/audit`, `POST /v1/loans/{id}/decision` (employee, `X-Approver`),
  `POST /v1/loans/{id}/notify`.

**Guarantees & enforcement.** Responses are validated against typed Pydantic
`response_model`s (FastAPI 422s on violation). **Versioned** under `/v1` (a breaking
change is a new path version, never a silent mutation). At the enterprise edge the
**API Gateway** enforces the OpenAPI schema + an API key (`x-api-key`); service-to-service
calls are private Cloud Run with **OIDC** (the BFF mints id-tokens). The contract returns
**only the intended fields** — this is what lets the agent and systematic integrations
consume it deterministically, with no hallucination or context-enrichment needed.

See [docs/05 — API architecture](05-api-architecture.md), [ADR-0006](adr/0006-api-gateway-vs-apigee.md).

## 2. Data contracts

Each of the 5 data products has a producer's versioned promise about its schema,
quality, SLAs, access model, and deprecation policy — authored as code and published
into the Universal Catalog as the `data-contract` aspect.

- **Artifacts:** [`contracts/<product>.yaml`](../contracts/) (deposit-transactions,
  customer-master, overdraft-history, loan-master, bank-knowledge-base) +
  [`contracts/README.md`](../contracts/README.md). Each declares `version` (semver),
  `status` (active/candidate/deprecated), schema + column classifications, `quality`
  rules, `sla` (freshness/availability), `access` (approval + groups), `lineage`, and
  `deprecationPolicy`.
- **Enforcement:** BigQuery **policy tags** (column-level security — PII stays masked
  for non-privileged readers), **Dataplex datascans** (the DQ rules become a live score
  on the `operational` aspect), partitioning/clustering, and the data-contract aspect on
  the entry so the contract is discoverable at search time.
- **Lifecycle:** edit YAML → bump `version` → PR review by the product owner →
  `python scripts/catalog_bootstrap.py <env>` republishes the aspect.

See [docs/12 — Knowledge Catalog §9](12-knowledge-catalog.md), [docs/13 — console snippets](13-data-product-console-snippets.md), [ADR-0011](adr/0011-dataplex-universal-catalog.md).

## 3. Event contracts

Every transaction event on the ingestion bus conforms to a registered schema, rejected
at the edge if it doesn't.

- **Artifact (publisher-facing):** [`transaction_message.schema.json`](../products/transactions/schemas/transaction_message.schema.json)
  — JSON Schema `TransactionMessage`: required `transaction_id`, `idempotency_key`,
  `account_id`, `txn_type ∈ {DEPOSIT,WITHDRAWAL,TRANSFER,FEE}`, `amount` (decimal-as-string
  to preserve NUMERIC precision), `currency` (`^[A-Z]{3}$`), `status ∈ {POSTED,PENDING,
  REJECTED}`, `event_time` (RFC3339); optional `counterparty_account` (`""` = none).
- **Artifact (enforced):** a Pub/Sub **Avro schema** (`google_pubsub_schema`, name
  `finchat-<env>-transaction-v2`) bound to the ingest topic via `schema_settings`
  (JSON encoding). Messages that don't match are **rejected at publish**; poison messages
  that fail downstream are routed to a **dead-letter topic** (DLQ).
- **Versioning:** the schema **name** is versioned (`-v2`). An incompatible change (e.g.
  union → string) can't be a schema *revision* — it's a **new schema name** and the topic
  re-points, so existing publishers never silently break. The raw payload also lands
  immutably in **Bronze** (`use_topic_schema = false`) for replay (ADR-0001).

See [docs/03 — Data flow](03-data-flow.md), [infra/modules/pubsub](../infra/modules/pubsub/).

---

## Why three contracts, not one

They sit at different boundaries and fail differently, so each is enforced where it
lives: the **event contract** keeps bad data off the bus (reject + DLQ); the **data
contract** keeps the at-rest product trustworthy and governed (CLS + DQ + certification);
the **API contract** keeps consumption deterministic and least-privilege (typed responses,
versioned, gateway + OIDC). Together they make the platform safe to consume *both*
systematically (apps/integrations) *and* agentically (ADK tools), which is the whole
point of grounding agentic AI on governed data.
