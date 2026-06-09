# FinChat Data Contracts

A **data contract** is the producer's binding, versioned promise to consumers about a
data product: its schema, semantics, quality, freshness/availability SLAs, access
model, and change policy. Contracts here are **code** (`<product>.yaml`) — reviewed in
PRs, and their summary is published to the Universal Catalog as the `data-contract`
**aspect** on each product's BigQuery entry (see `scripts/catalog_bootstrap.py`).

One file per built data product (maps 1:1 to a Dataplex **Data Product**, see
`scripts/data_products.py` and `docs/12-knowledge-catalog.md`):

| Contract | Data product | BigQuery table | Classification |
|---|---|---|---|
| `deposit-transactions.yaml` | Deposit Transactions | `silver.transaction` | PII_FINANCIAL |
| `customer-master.yaml` | Customer Master | `silver.customer` | PII_DIRECT |
| `overdraft-history.yaml` | Overdraft History | `gold.overdraft_history` | PII_FINANCIAL |
| `loan-master.yaml` | Loan Master | `loans.loan_status` | PII_FINANCIAL |
| `bank-knowledge-base.yaml` | Bank Knowledge Base | `kb.kb_chunks` | PUBLIC |

**Lifecycle:** edit the YAML → bump `version` (semver) → PR review by the product owner →
`python scripts/catalog_bootstrap.py <env>` republishes the contract aspect. Schema and
quality clauses are enforced in BigQuery (policy tags, partitioning, datascan rules) and
verified by the Dataplex data-quality scans that feed the **Insights** surface.

> Enterprise mapping: in a Fortune-500 deployment these contracts are enforced by a
> contract registry / CI gate (e.g., Data Contract CLI, Soda, or a custom admission
> controller). Here they are lightweight YAML + catalog aspects to demonstrate the
> pattern at near-zero cost.
