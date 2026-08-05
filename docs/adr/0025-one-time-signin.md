# ADR-0025 — One-time sign-in via the authorization-code flow

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Principal Data Architect
- **Context tags:** Identity, OAuth, UX, least privilege, credential handling

## Context

Staff signing in met **two consent screens**, the second landing mid-conversation.

The cause is structural, not a bug. Google Identity Services offers two grants and neither
produces the other in the browser:

| Grant | Gives | Needed for |
|---|---|---|
| `google.accounts.id` | **ID token** | Persona resolution ([ADR-0016](0016-identity-resolved-personas.md)) |
| `google.accounts.oauth2.initTokenClient` | **Access token** | BigQuery evaluating the user's own IAM and policy tags ([ADR-0019](0019-end-user-credential-propagation.md)) |

The SPA already tried to hide this by requesting the access token immediately after
sign-in. That call runs inside the GIS credential callback, which is **not a user-gesture
context**, so the browser blocked the popup. The lazy path then fired on the first chat
message — which *is* a gesture — and the user experienced it as being asked to log in
again. The code comment predicted exactly this outcome.

A second consent that appears once per account is a small thing. It is also the first
impression of a platform whose entire pitch is governed, identity-resolved access, and it
lands in the middle of a demo.

## Decision

Use the **authorization-code flow**, which returns identity, data access, and a refresh
token from a single consent.

```
[Sign in with Google]           our button — the click IS the gesture
   └─ initCodeClient(popup)     one screen: account + scopes
        └─ code → BFF /api/auth/exchange
             └─ id_token       → verified, persona resolved (unchanged)
                access_token   → returned to the tab, memory only
                refresh_token  → stored server-side
```

### Identity verification does not change

The `id_token` from the exchange goes through the **same** `verify_oauth2_token` call as
the GIS path — signature, audience, issuer, expiry, `email_verified`. This is why the
change needed no revision to ADR-0016's substance: what is verified and how is identical.
Only the number of consent screens moved.

The alternative — resolving identity from Google's `userinfo` endpoint using the access
token — was rejected precisely because it *would* have changed that. It replaces offline
cryptographic verification with a network call to a third party per identity check, which
is a real weakening for a banking reference implementation and not worth one popup.

### Scope narrowed to what is used

`cloud-platform` → `bigquery.readonly`. The old scope is the broadest Google publishes;
requesting it to run `SELECT`s produced an alarming consent screen and an obvious audit
finding. `openid email profile` are requested alongside it in the same grant.

### The refresh token is the point

Without it this is "one consent per session". With it, it is one consent **ever**:
`/api/auth/refresh` mints access tokens server-side with no user interaction.

Storage decisions, each deliberate:

- **Firestore, native mode** — already present in this project, scale-to-zero, encrypted
  at rest.
- **Keyed by SHA-256 of the email.** A collection of plaintext-email document ids is a
  roster of who uses the platform, readable by anyone who can list the collection without
  reading a single document. The body stores no email either, or the hashing is theatre.
- **Firestore unavailable ⇒ no persistence, not an in-memory fallback.** A refresh token
  cached in process memory disappears on the next cold start and presents to the user as
  *"it sometimes asks me to log in again"* — which is far harder to diagnose than a
  feature that never worked.
- **A missing `refresh_token` never clobbers a stored one.** Google issues one only on the
  first grant for a given client+user; treating its absence on re-consent as "clear the
  record" would silently un-persist the login.

### Sign-out revokes at Google

`/api/auth/signout` calls Google's revoke endpoint before dropping the local record.
Deleting only our copy would leave the grant live while the UI claims the user is signed
out — amnesia, not logout.

### The whole path is optional

With no client secret configured, `code_flow` is false and the SPA renders the original
GIS button with the two-grant path. A sign-in mechanism that hard-fails on a missing
secret is a worse trade than an extra consent screen.

## Consequences

**Positive**

- One consent, once, per user — not per session.
- Least privilege: `bigquery.readonly` instead of `cloud-platform`.
- Sign-out means what it says.
- Identity assurance unchanged; no weakening of the ADR-0016 story.

**Negative / accepted**

- **A client secret now exists and must be managed.** Held in Secret Manager
  (`finchat-oauth-client-secret`), mounted to the BFF only. This is new attack surface
  that the pure-GIS flow did not have, and it is the real cost of this decision.
- **FinChat now stores a long-lived credential for a human.** A refresh token is more
  sensitive than anything previously persisted here. Mitigated by hashed keys, encryption
  at rest, revoke-on-signout, and least-privilege scope — but the honest framing is that
  the blast radius of a Firestore compromise grew.
- **New runtime dependency on Firestore** for the BFF, plus `roles/datastore.user`.
- **Two sign-in paths to maintain** until the GIS fallback is removed.
- **Revocation outside the app is invisible to us** until a refresh fails. Handled by
  falling back to interactive consent, but there is no proactive signal.

## Alternatives considered

- **Single OAuth popup, identity from `userinfo`.** One consent and no client secret, but
  identity verification becomes a per-check network call instead of offline JWT
  verification. Rejected: ADR-0016 is a governance artifact and that is a real weakening.
- **Move the second grant into the sign-in click without changing flows.** Smallest change,
  keeps both grants — two consent screens back to back rather than one. Rejected as not
  meeting the requirement.
- **Service-account impersonation instead of user credentials.** Removes the consent
  entirely and destroys the point of ADR-0019: BigQuery would evaluate a platform identity,
  not the analyst's, so masking and CLS denial would no longer demonstrate anything.

## References

- [ADR-0016 — Identity-resolved personas](0016-identity-resolved-personas.md)
- [ADR-0019 — End-user credential propagation](0019-end-user-credential-propagation.md)
- [25 — End-to-end flow](../25-end-to-end-flow.md)
