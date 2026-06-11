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

## Decision (amended) — staff-only free-form CA + masked fallback + authorized views

- **Free-form Conversational Analytics is a staff surface, not a customer one.**
  The anonymous customer persona gets only the **DaaS-grounded banking assistant**
  (balance / transactions / summary / KB via tools) — it never reaches free-form
  Ask-the-Data. The CA endpoints (`/api/analyst/{ask,chat}`) are gated to the
  `analyst`, `employee` (loan approver), and `admin` personas; the gate answers
  *may you call this endpoint*, the user's own credentials answer *what you can see*.
- **Staff get CA + KB, never the DaaS serving-tier chat — by design, for
  governance consistency.** The DaaS banking-assistant tools (`get_account_balance`,
  `get_transaction_history`, `get_account_summary`, `get_loan_status`) are
  deterministic single-entity lookups against the **serving tier (Bigtable hot
  path)**, which has **no policy-tag masking**. Exposing them on the staff assistant
  would hand the masked analyst tier a **side door to real values** — reading
  unmasked balances/amounts through the serving API while CA returns NULL for the
  same columns — directly contradicting the per-user CLS/masking control this ADR
  establishes. So operational serving and governed analytics stay **separate paths**:
  every staff data question flows through CA, the one path where per-user enforcement
  lives. CA point-lookups are also an anti-pattern (LLM→SQL→BigQuery at a keyed read:
  slower, wrong tier, masked), so CA is not a substitute for DaaS — they answer
  different question classes and simply aren't in competition. The one persona whose
  job is single-applicant context, the **loan approver**, is served by the loans
  product's own agents (credit profile / overdraft / risk) plus CA over the views,
  and holds fine-grained reader anyway, so no masking inconsistency arises there. KB
  remains universal — it is non-sensitive published reference content with no governed
  columns.
- **The low-privilege SA is now a safe fallback, not a customer tier.** A dedicated
  **impersonated SA** (`finchat-<env>-analyst-anon`: jobUser, dataset READER on the
  semantic datasets, **maskedReader** on the PII_FINANCIAL data policy, and
  deliberately **no fine-grained read**) is used only when a *signed-in staff caller*
  has not yet propagated an OAuth token (consent pending) — they see **masked (NULL)
  protected values** rather than the BFF SA's broad access. Least privilege is the
  default: the BFF SA is never used for restricted callers, and restricted tiers
  never fall back to it on denial.
- **Enforcement is CLS/masking, not view-hiding — the decisive lesson.** Authorized
  views (graph dataset registered as an authorized dataset on the sources) let the
  views read silver on a caller's behalf, *but* Conversational Analytics, during
  planning, also resolves the underlying silver tables — and an LLM cannot be 100%
  constrained to query only the curated views. Relying on the views as the security
  boundary is therefore fragile. The robust model: restricted tiers get
  `bigquery.dataViewer` on silver, and **column-level security + data masking are the
  hard control** — `maskedReader` returns NULLs for protected columns, non-readers are
  denied, fine-grained readers see values. The semantic views remain the *preferred,
  clean* surface (instruction + agent context), but they are not the access boundary.
  Critically, **dataset access is delegated; policy-tag enforcement is never** — CLS
  evaluates against the end user regardless of how the table is reached, which is what
  makes one query path yield values / masked / denied per caller. Verified: the anon
  masked tier runs `total deposits by segment` and gets segments with `total_deposits =
  NULL`.
- Diagnostics: CA streams errors as **top-level `{"error":…}` entries in an
  HTTP-200 body**; the BFF parser captures these so restricted-tier denials map to
  the friendly request-access response instead of an empty answer.

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
