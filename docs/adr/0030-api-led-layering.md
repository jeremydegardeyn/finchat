# ADR-0030 — Name the API layers, and enforce the two rules that keep them apart

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Principal Cloud Architect
- **Context tags:** API-led connectivity, experience APIs, process APIs, BFF, separation of concerns

## Context

FinChat has had three API layers since Increment 3 and has never named them. The system
APIs are real and clean — `txn-api` and `loan-api` are contract-first, versioned, and own
their domains. The experience layer exists as `ui/server.py`. The process layer exists
too, but *inside* the BFF: the analyst router, the intent classification, the KB and
semantics handlers.

That mixing was invisible while there was one channel. [ADR-0028](0028-mcp-as-the-agent-channel.md)
created a second, and the evidence that the system layer was sound is that the MCP server
took a day and needed **zero** changes to it. The evidence that the middle layer was not
is harder to see, because nothing was broken — there was simply no place to put a rule
that two channels share.

The concrete question that forced this: a mobile home screen needs a balance, recent
activity, loan status, and a judgement about what the customer should do next. Three of
those are lookups. The fourth is a business rule, and there is currently nowhere to put it
that both the web and the mobile channel would read.

## Decision

**Name the three layers, give the process layer a service of its own, and enforce two
rules in CI.**

```
Experience   │  ui/server.py (web)   mcp_server/ (agent)   products/experience/mobile/
─────────────┼──────────────────────────────────────────────────────────────────────
Process      │  products/process/api/        ← composition + business rules
─────────────┼──────────────────────────────────────────────────────────────────────
System       │  txn-api      loan-api      agent      Conversational Analytics
             │  BigQuery Gold · CLS · Dataplex
```

- **`products/process/api/`** exposes `GET /v1/customers/by-account/{id}/overview`: one
  customer view across the transactions and loan domains, plus `next_action` — the single
  most useful thing this customer could do now. That decision is the reason the layer is
  not optional. It is a business rule, it will change, and if each channel owns a copy
  they will eventually disagree about whether a customer is in trouble.
- **`products/experience/mobile/`** exposes `GET /v1/home`: the same screen in **one**
  round trip, shaped for a phone. The web SPA makes three system calls for the equivalent
  view (`balance`, `summary`, `transactions`) plus one per loan. That delta is the
  measurable argument for the layer, and it is the number worth quoting rather than the
  diagram.
- **`GET /v1/loans` gained an `account_id` filter.** The composition needed loans for one
  account; the loan API could only return the queue. Selection belongs to the system that
  owns the rows — filtering in the process layer would have worked while moving 200
  records over the wire to keep two. This is the layering's first real test and it was
  resolved by changing the correct layer.

### The two rules, and why only two

`scripts/test_api_layering.py`:

| | Rule | What it prevents |
|---|---|---|
| **LAYER-1** | An experience API may not call a system API | a channel skipping the middle tier "just for this one field", which is how the process layer becomes optional and then wrong |
| **LAYER-2** | A process API may not touch a data store | the middle tier becoming a second copy of the domain model, which is how you get two definitions of a balance |

**Neither rule bans passthrough**, and that matters. An experience API forwarding a single
resource unchanged is fine — the BFF does exactly that for `/api/txn/*` and is right to.
The rules are about *reaching around* the process layer to compose, and about the middle
tier acquiring its own schema. Everything else is left to review, because a rule nobody
can satisfy gets an exception list and then gets deleted.

The guard reads the **AST**, not the source text. Its first version failed on
`process/api/sources.py`, whose docstring says that importing BigQuery would break the
layering — a rule that fires on the prose explaining the rule is one people fix by
deleting the explanation.

### The rule that keeps an experience API honest

**It may aggregate, reshape, filter, format and paginate. It may not decide.** If the
mobile and web channels can disagree about whether a loan is approvable, the logic is in
the wrong layer. `test_home.py` asserts structurally that no business vocabulary
(`PENDING_APPROVAL`, `overdraft`, balance comparisons) appears in the mobile channel,
because that drift would be gradual and every individual step would look defensible.

## Consequences

- A business rule spanning domains now has one home, and a second channel proving it.
  `next_action` is computed once and rendered twice.
- **Two new services that are not deployed.** They run, they are tested, and no Terraform
  or Cloud Run configuration exists for them yet — the same position `mcp_server/` is in,
  and for the same reason: standing them up is a cost decision that has not been taken.
- **The analyst router is split by what can move.** The routing decision — tables,
  prompt, precedence, classifier ordering — now lives in the process layer, and doing so
  collapsed two copies of the precedence rules into one. The handlers stay in the BFF
  because they carry the end-user's OAuth token ([ADR-0019](0019-end-user-credential-propagation.md))
  and the gateway's `on_behalf_of`; moving those means forwarding end-user credentials
  between services, which is a security design change rather than a refactor. The rule is
  shared, the credential-bound execution is not.
- The process layer degrades per source: an unreachable loan API costs the customer their
  loan section, named in `partial`, not their balance. A channel can then say what is
  missing instead of showing a spinner or a confidently incomplete screen.
- One more hop for composed reads. Real latency, paid once per screen instead of three
  round trips from a phone, which is the trade that favours it on mobile and would not on
  a LAN.

## Alternatives considered

- **Leave the process logic in the BFF and add mobile routes there.** No new services, no
  new hop. Rejected: it is precisely what makes a BFF grow into the monolith the layering
  exists to prevent, and with two channels the shared rule has to live somewhere neither
  owns.
- **Let the mobile API call the system APIs directly and compose there.** One fewer hop
  and faster to write. It duplicates `next_action` into a second codebase on day one —
  the exact drift this ADR is about — which is why LAYER-1 is a test rather than a note.
- **Extract the analyst router first.** The most valuable process capability, and the
  riskiest: it is live in prod on every analyst question. Deliberately sequenced after a
  cheap channel proves the seam.
- **GraphQL for the mobile channel.** Solves chattiness generically and is a reasonable
  answer at a larger client count. It moves composition to the client's query rather than
  giving the business rule a home, which is the actual problem here.
