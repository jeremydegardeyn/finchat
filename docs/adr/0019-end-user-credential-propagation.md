# ADR-0019 — End-user credential propagation for conversational analytics

- **Status:** Accepted
- **Date:** 2026-06-11
- **Deciders:** Principal Cloud Architect
- **Context tags:** Identity, least privilege, OBO, column-level security, data masking

## Context

After ADR-0016 (identity-resolved personas) the BFF *knows who you are*, but the
service account still *fetched the data* — so the SA's permissions were every
user's effective permissions, and ADR-0018's perimeter was enforced by agent scope
and view shape rather than by IAM evaluated per user.

## Decision

Propagate the signed-in user's **OAuth access token** to the analytics path so
**BigQuery evaluates the user, not the service account**:

- **SPA:** after sign-in, staff grant a one-time OAuth consent (GIS token client,
  `cloud-platform` scope, hinted to the signed-in account). The access token
  (~1h, auto-refreshed) is sent as `X-User-Access-Token`.
- **BFF:** `_run_ca` prefers the user token for Conversational Analytics calls
  (Data Agent first, inline fallback). The SA path remains as fallback when no
  token is supplied, so nothing breaks.
- **Three access tiers, decided by BigQuery + the policy-tag stack:**
  | Principal | Grants | Result on protected columns |
  |---|---|---|
  | jeremy@ (approver) | fine-grained reader | real values |
  | jdegardeyn@ (analyst) | jobUser + dataset READER + **maskedReader** on the `PII_FINANCIAL` **data policy** (`finchat_prod_pii_financial_mask`, ALWAYS_NULL) | **masked (NULL)** values |
  | datadinosaur.noreply@ (admin) | jobUser + dataset READER only | **CLS-denied** → friendly message |
- **Friendly denial UX:** 401/403 (no access) and query-time CLS denials map to a
  "request access from the data product owner" message **linking to the Dataplex
  Data Products page** — the access-request/approval flow built in Increment 10 is
  the fulfillment mechanism. Governance loop closed: deny → request → approve →
  IAM grant on the product's assets.
- **Ask-the-Data is now a staff surface, not analyst-only:** the loan approver and
  platform admin personas get the Analyst tab; the persona gate answers *may you
  call this endpoint*, while the user's own credentials answer *what data can you
  see*. Authorization moves to the data layer, where it belongs.
- **BigQuery data masking is now real** (was a docs/09 roadmap item): a
  `DATA_MASKING_POLICY` on the `PII_FINANCIAL` tag returns NULLs to masked
  readers instead of erroring — analytical SQL keeps working for the analyst tier.

## Consequences

- First Ask-the-Data use per staff account triggers a Google consent popup
  (sensitive scope; Testing-mode test users click through the unverified-app
  interstitial once).
- The eval-capture and KB paths still run under the SA (they don't read governed
  analytical columns); the catalog search remains SA-backed (read-only metadata).
- Remaining enterprise step: a dedicated analyst SA and removal of the BFF SA's
  broad `bigquery.dataViewer` once all analytics traffic carries user credentials.
- Demo caveat: tokens expire hourly — staff re-consent silently (`prompt:""` with
  account hint) unless the session is fully signed out.

## Alternatives considered

- **BFF entitlement checks with SA execution:** authorization simulated at the
  gateway; rejected as primary (BFF bugs could leak; data layer should decide).
- **Domain-wide delegation / token exchange to impersonate users:** wrong tool —
  the user is present and can consent; standard OAuth is simpler and auditable.
- **Per-persona Data Agents with different table scopes:** complementary (still
  available for a privileged-identifier tier) but doesn't give per-user IAM truth.
