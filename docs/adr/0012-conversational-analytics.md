# ADR-0012 — Employee (Analyst) persona: catalog discovery + Conversational Analytics

- **Status:** Accepted
- **Date:** 2026-06-09
- **Deciders:** Principal Data Architect
- **Context tags:** Personas, self-service analytics, AI, governance

## Context

FinChat had three personas — Customer, Employee (Loan Officer), Admin. Two
capabilities were mis-placed or missing:

1. **Catalog discovery** ("find data by description") was wired into the *Customer*
   banking agent (`discover_data_product` tool) — wrong audience; customers don't
   browse the data catalog, analysts do.
2. There was no **self-service analytics** surface — an analyst could not ask
   natural-language questions over the governed data products.

## Decision

Add an **Employee (Analyst)** persona with a dedicated view exposing two features,
served by the existing UI BFF (no new service):

1. **Knowledge Catalog discovery** — `/api/catalog/search` queries the Dataplex
   Universal Catalog (`search_entries`) by free-text description and returns the
   governed aspects (domain, owner, criticality, certification, PII class, contract
   version, DQ). Removed `discover_data_product` from the customer agent.
2. **Conversational Analytics** — `/api/analyst/chat` calls **Google's
   Conversational Analytics API** (`geminidataanalytics.googleapis.com`, "Data
   Analytics API with Gemini") via its **stateless `:chat`** method, with an inline
   BigQuery datasource (the analytical products: `transaction`, `customer`,
   `overdraft_history`, `loan_status`). It returns a grounded NL answer, the
   **generated SQL**, the **result rows**, and follow-up questions.

The UI BFF service account (`txn-api`) is granted
`roles/geminidataanalytics.dataAgentStatelessUser` (permission
`geminidataanalytics.locations.chat`) and `roles/dataplex.catalogViewer`; it already
holds project `bigquery.dataViewer` + `jobUser`, so the API's generated SQL runs
under least-privilege (PII columns stay masked by policy tags unless the SA is a
fine-grained reader).

## Rationale

- **Right capability, right persona:** discovery and analytics are analyst tools;
  the customer agent stays scoped to a customer's own account/loan/KB questions.
- **Managed conversational AI over governed data:** the Conversational Analytics API
  is Google's purpose-built NL→BigQuery engine — no agent/SQL to hand-build, and it
  reads the *same* governed data products (grounding + lineage preserved).
- **Stateless, near-zero cost:** the `:chat` inline-context call needs no persistent
  data agent; it is pay-per-use Gemini and scales to zero with the BFF.
- **Reuses the BFF:** one Cloud Run service, one SA, Model-Armor path unchanged.

## Consequences

- The Conversational Analytics API (preview) returns a **streamed JSON array** of
  system messages (THOUGHT / FINAL_RESPONSE / FOLLOWUP_QUESTIONS, plus `data` with
  `generatedSql` and `result`); the BFF reduces it to `{answer, sql, columns, rows,
  followups}`.
- The analyst SA can read all analytical datasets; CLS still masks PII at query time.
- `discover_data_product` (agent tool) is retained as a reusable helper but no longer
  wired into the customer agent (catalog discovery now lives in the BFF). Fixed a
  latent bug: aspect `data` is a proto-plus `MapComposite`, converted with `dict()`
  not `MessageToDict`.
- Deploy: UI + agent images rebuild via CI/CD; `terraform apply` grants the new SA
  roles. Datasets must be co-located (`us-central1`) for the inline BQ datasource.

## Alternatives considered

- **Build a custom NL→SQL agent (ADK + Gemini + BigQuery tools):** rejected — reinvents
  a managed, governed service; more eval/guardrail burden.
- **Keep discovery in the customer agent:** rejected — wrong audience; leaks catalog
  surface area to customers.
- **Persistent Conversational Analytics data agent:** deferred — stateless `:chat`
  meets the demo need without lifecycle management; revisit for saved context/history.
