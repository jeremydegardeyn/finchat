# ADR-0018 — Analyst semantic perimeter + persistent Data Agent

- **Status:** Accepted
- **Date:** 2026-06-11
- **Deciders:** Principal Cloud Architect
- **Context tags:** Conversational analytics, least privilege, semantic layer, governance

## Context

Probing the analyst chat surfaced two gaps (user-found, during interview-prep review):

1. Conversational Analytics was grounded on **silver entity tables** (added for the
   knowledge-graph join fix). Silver is the *canonical* layer, not a consumption
   layer — and `silver.account` carries `account_number`, which shares the
   `PII_FINANCIAL` policy tag with `amount`. Because the analyst path's SA must read
   that tag (amounts are the product), **CLS could not stop a chat-generated query
   from returning account numbers**; a system-instruction line was the only barrier.
2. The CA context (tables + system instruction) was assembled **per request in the
   BFF** — configuration as payload, with no central governance or versioning.

## Decision

1. **Semantic perimeter.** Conversational analytics sees ONLY curated serving
   surfaces in `finchat_graph_<env>`: `dim_customer`, `dim_account`,
   `fact_transaction` (+ existing `customer_360`, `kg_relationships`) plus the
   gold/loans products. The dim/fact views **structurally omit identifier columns**
   (`account_number`, `counterparty_account`, `full_name`, `email`, natural keys) —
   data minimization by view definition, not by prompt. No silver tables in scope;
   the medallion contract holds (silver = canonical, consumption via curated layers).
2. **Persistent Data Agent.** The context now lives in a first-class Gemini Data
   Analytics **`dataAgents` resource** (`finchat-<env>-analyst`): the semantic-
   perimeter tables + system instruction are a governed, versioned, IAM-controlled
   resource. The BFF chats via `data_agent_context` (SA role:
   `geminidataanalytics.dataAgentUser`) and **falls back to inline context** where an
   env has no agent (dev/test today). Layered controls remain: instruction tells the
   model identifiers "are not available on the analyst surface"; the views make that
   physically true; CLS guards the source tags underneath.

## Verified (prod)

- *"show me account numbers for some PREMIER customers"* via the agent → generated
  SQL touches **no silver table, no account_number**; answer states identifiers are
  not available on this surface.
- *"total deposits by customer segment"* → correct results from
  `fact_transaction`/`customer_360` joins (semantic layer answers real analytics).

## Consequences

- New views in `products/graph/schemas/graph.sql` (applied to all envs); agent
  created in prod (`scripts`-free: plain REST, metadata-only, $0); UI deploy gains
  `DATA_AGENT_ID`; foundation adds `dataAgentUser` to the BFF SA.
- Granular questions analysts could previously ask against silver still work — the
  fact/dim views project the same analytical columns.
- Remaining hard-control work (documented in docs/09): split the policy-tag taxonomy
  (FINANCIAL_VALUE vs IDENTIFIER) + BigQuery data masking; and a dedicated analyst
  SA (the BFF SA still holds project-wide `bigquery.dataViewer`, so the perimeter is
  enforced by agent scope + views, not yet by IAM denial).

## Alternatives considered

- **Instruction-only control:** rejected — soft controls are never the sole barrier
  for data a role must not see.
- **Dropping the SA's PII_FINANCIAL grant:** impossible without breaking amounts —
  the tag conflates identifiers with values (hence the taxonomy-split roadmap).
- **Authorized views on a dedicated analyst dataset with its own SA:** the full
  enterprise endgame; the semantic perimeter + agent is the right-sized step that
  delivers the structural guarantee now at $0.
