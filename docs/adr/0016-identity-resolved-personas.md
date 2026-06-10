# ADR-0016 — Identity-resolved personas (Google Sign-In + BFF-enforced RBAC)

- **Status:** Accepted
- **Date:** 2026-06-11
- **Deciders:** Principal Data Architect
- **Context tags:** Security, identity, authorization, audit

## Context

Personas were simulated: a dropdown set an `X-Persona` header the BFF trusted, and
employee actions injected a **client-supplied** `X-Approver` — meaning the approver
identity in the append-only loan audit trail was spoofable. The original requirement
was principal-aligned personas: a customer can *create* a loan but never *approve*
one, and staff surfaces (loan ops, analytics, admin) belong to specific people.

## Decision

Resolve personas from **verified Google identity**, enforced at the BFF:

- **Google Identity Services** ("Sign in with Google") in the SPA. The persona
  dropdown is removed when auth is configured. Signed out → **Customer** view
  (no login, frictionless). Staff sign in; the GIS ID token is sent as
  `X-User-Token` on API calls.
- The **BFF verifies** the token (signature via Google certs, audience = our OAuth
  client, issuer, expiry, `email_verified`) with `google-auth`, then maps the email
  to a persona from env config (`APPROVER_EMAILS` / `ANALYST_EMAILS` /
  `ADMIN_EMAILS`): approver → Loan Officer view, analyst → Analyst view, admin →
  Admin view. Unknown signed-in users fall back to Customer.
- **Server-side route enforcement** (not hidden buttons): loan review queue, audit,
  and `POST …/decision` require the verified **approver**; `/api/analyst/*` and
  `/api/catalog/*` require the **analyst**; `/api/eval` requires the **admin**.
  Loan create / status / notify and the customer chat stay open.
- **Verified approver in the audit trail:** on decisions the BFF sets `X-Approver`
  to the *authenticated* email; the client-supplied value is ignored. The immutable
  `approval_decision` / audit rows now record who really approved (dual-control /
  adverse-action evidence).
- **Graceful fallback:** if `GOOGLE_OAUTH_CLIENT_ID` is unset, the app behaves as
  before (dropdown simulation) — dev/test keep working without OAuth setup.

## Rationale

- **Not IAP:** IAP would gate the whole app behind login; the requirement is an
  anonymous customer surface with staff elevation. GIS + BFF verification is the
  standard "public app, optional staff sign-in" pattern.
- **BFF as the enforcement point** is the correct gateway pattern: terminate user
  identity at the edge, run backends on per-service least-privilege service
  accounts, propagate user context (the verified approver email) where it matters.
  End-user credential passthrough to BigQuery et al. is deliberately out of scope.
- The OAuth **client ID is public by design** (it ships in the page); no secret is
  stored. The consent screen is External/Testing with the three principals as test
  users (one admin principal is a plain Gmail account, which rules out Internal).

## Consequences

- UI deploy gains `GOOGLE_OAUTH_CLIENT_ID`, `APPROVER_EMAILS`, `ANALYST_EMAILS`,
  `ADMIN_EMAILS`. Authorized JavaScript origins must list the exact UI URLs (both
  Cloud Run URL formats; add the custom domain when mapped).
- GIS ID tokens expire after ~1h — staff re-sign-in mid-session is expected; the
  401/403 message says so. For a demo, sign in fresh beforehand.
- GCP **IAM mirroring** for the human principals is optional and behaviorally inert
  (the BFF SA executes all data access). The domain-restricted-sharing org policy
  may block IAM grants to the Gmail admin; app-level auth is unaffected.
- Customer identity remains simulated (account ids) — a future increment can bind
  customers to sign-in too.

## Alternatives considered

- **IAP on Cloud Run:** rejected — gates anonymous customers out (see Rationale).
- **Identity Platform / Firebase Auth:** heavier dependency for the same "Sign in
  with Google" outcome; revisit if multi-IdP (e.g. SAML) is needed.
- **Keep persona simulation:** rejected — spoofable approver identity in a credit
  audit trail is exactly what a regulator (and an interview panel) would flag.
