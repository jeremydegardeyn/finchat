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

## Enterprise mapping (dual-tier)

In the Fortune-500 build the same persona model maps to managed identity services:

- **Staff surfaces → IAP (Identity-Aware Proxy).** Split the staff app (loan ops,
  analyst, admin) onto its own Cloud Run service/origin and put **IAP** in front of
  it (Cloud Run's native IAP integration, or a HTTPS LB + serverless NEG). IAP
  centralizes AuthN at Google's edge, supports **context-aware access** (device,
  IP, time), and passes a **signed assertion** (`X-Goog-IAP-JWT-Assertion`) the app
  verifies — replacing this ADR's in-app token check. Access is then IAM
  (`roles/iap.httpsResourceAccessor`) per principal/group, i.e. persona = IAM group.
- **Customer surface → CIAM**, e.g. **Identity Platform** (OIDC/SAML, MFA, tenant
  per brand) — customers authenticate too, bound to their accounts; the demo's
  anonymous-customer mode is the placeholder for this.
- **Workforce IdP federation:** staff identities come from the bank's IdP (Entra
  ID/Okta) via Workforce Identity Federation rather than consumer Google accounts.

IAP wasn't used **here** because it gates the entire service behind login, and the
demo intentionally serves an anonymous customer surface and a staff surface from
one app; GIS + BFF verification delivers the same verified-identity guarantees on
one origin at zero cost.

## Alternatives considered

- **IAP on Cloud Run (single app):** rejected for the demo — gates anonymous
  customers out; **it is the enterprise target for the staff surface** (above).
- **Identity Platform / Firebase Auth:** heavier dependency for the same "Sign in
  with Google" outcome; the enterprise target for the *customer* surface.
- **Keep persona simulation:** rejected — spoofable approver identity in a credit
  audit trail is exactly what a regulator (or any serious security review) would flag.
