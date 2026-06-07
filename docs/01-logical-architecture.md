# 01 — Logical Architecture

> Capability/layer view of the platform, independent of specific GCP resources (those are in
> [02](02-physical-architecture.md)). Shows how the two data products, the serving layer, agents, and
> governance fit the data-mesh model.

```mermaid
flowchart TB
  subgraph sources["Sources"]
    GEN[Synthetic transaction generator]
    CUST[Loan applicants]
  end

  subgraph ingestion["Ingestion & Real-Time Processing"]
    EVENT[Event backbone / Pub-Sub]
    STREAM[Stream processing + DQ + DLP]
    DLQ[(Dead-letter queue)]
  end

  subgraph product1["Data Product 1 — Transactions (Medallion)"]
    BRONZE[(Bronze · raw)]
    SILVER[(Silver · conformed + masked)]
    GOLD1[(Gold · serving views)]
  end

  subgraph product2["Data Product 2 — Loans"]
    LREQ[(Loan requests)]
    PROF[(Credit profiles)]
    RISK[(Risk assessments)]
    DEC[(Approval decisions · append-only)]
  end

  subgraph serving["Data-as-a-Service"]
    DAAS[DaaS APIs · OpenAPI]
    GW[API gateway]
  end

  subgraph intelligence["Agentic AI"]
    CHAT[Banking Assistant]
    LOANAG[Loan multi-agent + workflow]
    HITL[Human approver]
  end

  subgraph governance["Cross-cutting Governance & Platform"]
    GOV[Classification · CLS/RLS · lineage · audit]
    SEC[IAM least-privilege · encryption]
    OPS[CI/CD · IaC · monitoring · eval]
  end

  subgraph experience["Experience"]
    UI[Customer / Employee / Admin UI]
  end

  GEN --> EVENT --> STREAM --> SILVER
  EVENT --> BRONZE
  STREAM -.invalid.-> DLQ
  BRONZE --> SILVER --> GOLD1
  GOLD1 --> GW --> DAAS
  DAAS --> CHAT
  CUST --> UI --> DAAS
  UI --> LOANAG
  LOANAG --> LREQ & PROF & RISK
  LOANAG -->|overdraft via DaaS| DAAS
  LOANAG --> HITL --> DEC
  CHAT --> UI
  governance -.governs.- product1
  governance -.governs.- product2
  governance -.governs.- serving
```

## Layers

| Layer | Responsibility | Key principle |
|-------|----------------|---------------|
| Sources | Produce raw events / requests | Decoupled from consumers |
| Ingestion & RT | Transport, validate, mask, route | Event-driven, schema-enforced, DLQ |
| Data Products | Own curated, governed data | Data as a Product (medallion; append-only loans) |
| Data-as-a-Service | Expose governed data | API-first, contract-driven |
| Agentic AI | Reason + act over data | Grounded, tool-calling, evaluated |
| Governance/Platform | Cross-cutting controls | Least privilege, lineage, IaC, AgentOps |
| Experience | Role-based UX | Persona-scoped access |

## Data-mesh framing

Each data product is independently owned, deployable, and governed, yet **interoperable**: the Loan
product consumes the Transaction product's overdraft signal through the same governed DaaS contract
every other consumer uses — federated computational governance, not point-to-point coupling.
