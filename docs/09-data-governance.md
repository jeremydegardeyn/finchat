# 09 — Data Governance Strategy

> How FinChat governs data across its lifecycle: classification, quality, metadata, lineage, access,
> retention, and stewardship. Security controls: [06](06-security-architecture.md). Model: [data-model](data-model.md).

## Governance operating model

Federated, product-aligned governance with central guardrails (data-mesh): each **data product owns**
its schema, quality, and access contract; the **platform enforces** common policy (classification,
DLP, audit, IaC) so standards are consistent without a central bottleneck.

## 1. Classification

Five-tier taxonomy (`PII_DIRECT`, `PII_FINANCIAL`, `CONFIDENTIAL`, `INTERNAL`, `PUBLIC`) implemented as
**Data Catalog policy tags** and applied to columns at the Silver layer. Drives column-level access and
masking. See the [data-model classification table](data-model.md#classification-taxonomy).

## 2. Data quality

| Dimension | Control | Where |
|-----------|---------|-------|
| Validity | Schema enforcement + field/enum/format validation | Pub/Sub Avro schema + Beam `parse_and_validate` |
| Completeness | Required-field checks | pipeline validation → DLQ on miss |
| Uniqueness | Idempotency-key dedup (`insertId` / MERGE) | Beam → BigQuery Silver |
| Integrity | FK conventions, append-only decisions | schema design |
| Timeliness | Stream processing + DLQ backlog alert | Dataflow + monitoring |
| Quarantine | Dead-letter queue with error reason | `transactions-dlq` + 7-day triage sub |

## 3. Metadata & catalog

- **Technical metadata:** table/column descriptions, partition/cluster declared in Terraform + DDL.
- **Business metadata:** policy-tag taxonomy = the business classification glossary.
- **Operational metadata:** `pipeline_version`, `source_system`, `ingest_time` on every row.
- **Discovery:** Dataplex/Data Catalog (enterprise scale) for search, profiling, and tag templates.

## 4. Lineage

Captured along `Pub/Sub message_id → Bronze.transaction_event → Silver.transaction (idempotency_key)
→ Gold views → DaaS API → Agent / Loan product`. Inline provenance columns + Dataplex lineage API at
scale. **Cross-product lineage:** loan `risk_assessment` ← transaction `gold.overdraft_history`.

## 5. Access governance

- Least-privilege IAM + custom roles; column-level (policy tags) and row-level (RAP) security.
- **Authorized views**: consumers read Gold without any grant on Silver.
- Privileged PII access limited to a fine-grained-reader group; everything audited.

> **Bronze is raw by design.** The Bronze landing (`transaction_event.data`) stores the exact
> published payload, **un-de-identified** — it is the immutable audit/replay source of truth. The
> control for Bronze is **restricted access** (only the pipeline service account + admins;
> no `viewer_members` granted), short partition expiration, encryption, and audit logging — **not**
> masking. **De-identification (DLP) is applied on promotion Bronze/stream → Silver**, which is the
> broadly-consumed layer. This is the standard medallion contract: lock down raw, govern the curated copy.

### Known refinement: split identifiers from values in the taxonomy

A finding from probing the analyst conversational surface: `account_number` and `amount`
share the **PII_FINANCIAL** tag, and the serving SA must read that tag (analytics needs
amounts) — so CLS alone cannot stop a chat-generated query from returning **account
numbers** to an analyst. Today's control is the model **system instruction** (soft;
never return names/emails/account numbers). The enterprise fix is structural:

1. **Split the taxonomy** — `PII_FINANCIAL_VALUE` (amount, balance — analysts read) vs
   `PII_IDENTIFIER` (account_number — analysts masked/denied).
2. **BigQuery data masking** on the identifier tag (`Masked Reader` → analysts see a
   hash/NULL instead of a hard error), so analytical queries keep working while
   identifiers stay protected.

This layers controls correctly: hard (CLS/masking) for what roles must never see,
soft (instructions) only for tone/shape — never as the sole barrier. `full_name`/`email`
already demonstrate the hard layer (PII_DIRECT, no analyst-path grant → denied through
SQL, GQL, or chat-generated queries alike).

## 6. Retention & lifecycle

| Data | Retention | Mechanism |
|------|-----------|-----------|
| Bronze events | 400d → GCS cold | partition expiration + bucket lifecycle |
| Silver/Gold | 7y (regulatory) | table/partition policy |
| Loan decisions & audit | 10y immutable | append-only + locked log bucket |
| RTBF | crypto-shred | delete DLP deterministic key |

## 7. Stewardship & RACI (illustrative)

| Activity | Product Owner | Platform Team | Security/Compliance |
|----------|:-:|:-:|:-:|
| Schema & contract | **R/A** | C | C |
| Classification tags | R | **A** | C |
| DLP / masking policy | C | R | **A** |
| Access approvals | R | C | **A** |
| Retention policy | C | R | **A** |
| Incident / DLQ triage | **R** | A | I |

## 8. Compliance alignment

Designed to support **GLBA / SOX / PCI-DSS / BCBS 239 / GDPR-CCPA** controls: provenance &
reconcilability (BCBS 239), PII safeguarding (GLBA/GDPR), immutable audit (SOX), cardholder data
tokenization (PCI), and auditable, versioned credit decisions (SR 11-7 model risk).
