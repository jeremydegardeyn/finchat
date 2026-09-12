# ADR-0033 — Align the ontology and the system APIs to BIAN, as annotations

- **Status:** Accepted
- **Date:** 2026-09-12
- **Deciders:** Principal Cloud Architect
- **Context tags:** BIAN, service domains, industry standards, ontology, semantic interoperability, agent grounding, API-led connectivity

## Context

FinChat's conceptual model is five classes in [`knowledge/ontology.yaml`](../../knowledge/ontology.yaml),
two system APIs, one process API, and a vocabulary that is entirely its own. That was
fine while the only consumers were the platform's own agents, which are grounded on the
compiled OKF bundle and cannot disagree with it ([ADR-0014](0014-knowledge-graph-semantic-layer.md),
docs/20). It stops being fine at the boundary: a third-party MCP client
([ADR-0028](0028-mcp-as-the-agent-channel.md)), an enterprise architect mapping FinChat
onto a bank's capability model, or a foundation model asked what "Position Keeping" has to
do with `fact_transaction` has no bridge between FinChat's names and the names the
industry uses.

The industry names are BIAN's. The [Banking Industry Architecture Network](https://bian.org)
publishes a **Service Landscape** — about three hundred *service domains*, each owning one
business capability with one *control record*, and a fixed grammar of *action terms*
(Initiate, Retrieve, Update, Evaluate, Notify, …) that its semantic APIs are expressed in.
Banks use it as the shared vocabulary for capability maps, vendor evaluation and API
design. It is also, read a different way, an ontology: a controlled vocabulary of banking
concepts with a boundary rule between them.

Two things are true about BIAN that shape what to do with it here:

1. **FinChat already obeys BIAN's central rule without naming it.** BIAN's
   service-domain boundary is that a domain owns its control record and *composition
   across domains lives outside every domain*. [ADR-0030](0030-api-led-layering.md)
   reached the same rule from the other direction: `GET /v1/loans?account_id=` was added
   to the loan API because selection belongs to the system that owns the rows, and
   `next_action` lives in the process layer because it spans two domains. The layering is
   already BIAN-shaped. What is missing is the vocabulary.
2. **Adopting BIAN as a target architecture would be the wrong size.** The Service
   Landscape is for mapping a real bank's estate. FinChat's five classes map onto five
   service domains; a re-platforming to BIAN's semantic API shapes would replace working,
   contract-first APIs with ones nobody here consumes, for a benefit that is entirely
   about naming.

The choice was therefore never "BIAN or not". It was where the alignment lives, whether
it is enforced, and whether the agents get to read it.

## Decision

**Annotate, do not re-platform. Compile the annotations into the grounding. Guard them
in CI.**

Three concrete changes:

1. **`knowledge/ontology.yaml` carries the alignment.** A `standards.bian` block names
   the landscape version, the SKOS mapping vocabulary used (`exactMatch` /
   `closeMatch` / `relatedMatch`) and the BIAN action-term set. Every class gains a
   `bian:` block naming the service domain(s) it aligns to and the strength of the
   match. A new `capabilities:` section lists every `/v1` operation on the system and
   process APIs and names the BIAN service domain and action term each implements — or
   states explicitly that the operation is platform machinery with no counterpart in
   the landscape. The ontology was already the single source of truth for what the
   concepts *are*; it is now also the single source of truth for what the industry
   calls them.
2. **The alignment is a compiled projection, like everything else in the bundle.**
   `scripts/compile_ontology.py` renders it into
   [`knowledge/reference/bian-alignment.md`](../../knowledge/reference/bian-alignment.md),
   which sits in a directory the OKF compiler already sweeps into `ANALYST_KNOWLEDGE`.
   So the analyst semantics route, `describe_data_model` and `search_knowledge_base`
   on the MCP server can all answer "which BIAN service domain is this?" from the same
   text, and the MCP server additionally publishes it as JSON at
   `finchat://knowledge/bian` for a client that wants the mapping rather than the
   prose. The routing tables gain the standard's vocabulary so the question lands on
   the semantics route rather than being mistaken for a platform question.
3. **Two drift guards in `scripts/test_ontology.py`.** Every class must carry a BIAN
   alignment whose service domain is in the declared landscape subset and whose match
   is in the SKOS vocabulary. And every `/v1` operation actually declared in the
   transactions OpenAPI contract and the loan / process API route decorators must appear
   in `capabilities:` — the guard reads the contract and the route decorators, so an
   endpoint added without a service-domain annotation fails CI rather than quietly
   widening the surface the standard does not describe.

### The alignment itself

| FinChat class | BIAN service domain | Match | Why not exact |
|---|---|---|---|
| `Customer` | Party Reference Data Directory | close | FinChat's Customer collapses BIAN's *Party* (the person) and *Customer Agreement* (the relationship); `segment` is a relationship property |
| `Account` | Current Account · Savings Account | close | one class, two BIAN domains — `account_type` selects which |
| `Transaction` | Position Keeping | close | the financial position log, POSTED-only; BIAN also keeps pending items, which FinChat filters at the serving view |
| `OverdraftProfile` | Customer Behavior Insights | related | a derived behavioural analytic, not a facility property — hence *related*, not *close* |
| `Loan` | Customer Offer | close | FinChat's Loan is an **origination** record (`requested → approved/denied`); a fulfilled loan would be BIAN's *Consumer Loan*, which is not modelled |

The honest column is the last one. A mapping that claims `exactMatch` everywhere is one
nobody checked; the mismatches are where an integrator will otherwise be surprised.

The system APIs map cleanly: every transactions read is `Position Keeping · Retrieve`
except the summary, which is `Current Account · Retrieve` over the facility; every loan
operation is `Customer Offer` with the action term that names what it does (`Initiate`
for submission, `Evaluate` for the approver's decision, `Notify` for the callback). The
process API's customer overview is annotated as a BIAN **business scenario** spanning four
domains — which is exactly the thing BIAN says should not live inside any one of them,
and is where ADR-0030 already put it.

## Consequences

- **The agents can now answer in the industry's vocabulary without inventing it.** Ask
  the analyst semantics route which service domain a balance belongs to and it answers
  from the compiled alignment, not from whatever the foundation model recalls about BIAN.
  A remote MCP client gets the same mapping as JSON.
- **The alignment cannot rot.** A new system-API endpoint, or a new class, fails CI until
  it says what BIAN calls it. This is the same discipline as the classification guard in
  Inc 21 (`classification:` must be a real taxonomy term): the ontology may reference a
  standard, but it may not invent one.
- **Alignment is to the public Service Landscape, not the Business Object Model.** BIAN's
  full BOM is member-gated. Referencing service-domain and action-term names is fine;
  reproducing the object model would not be, so the ontology's own properties remain its
  own and the mapping is at the concept level. This is stated in the file so nobody
  "completes" the mapping later.
- **`Household` stays unmapped, deliberately.** It is `modelled: false` in the glossary
  and a `refuse` golden query. BIAN has a place for it (Party relationships); giving it a
  BIAN alignment would suggest it exists here, and the whole point of that term is that
  it does not.
- **No runtime change.** The APIs, their paths, contracts and gateway configuration are
  untouched. An integrator who wants BIAN-shaped semantic APIs (`/current-account/{cr-id}/retrieve`)
  would add them as an *adapter* over the system layer — a legitimate future increment,
  and the annotations are the specification for it.

## Alternatives considered

- **Rename the APIs and classes to BIAN's terms.** The purest alignment and the most
  disruptive: every contract, view, test and prose doc changes, the graph view's
  relationship labels change, and the names get *worse* for the people who use them
  (`fact_transaction` says what it is; `Financial Position Log` does not, to an analyst).
  Rejected. The standard is for the boundary, not the local vocabulary.
- **Publish BIAN semantic APIs alongside the system APIs.** Real interoperability value
  for a bank with BIAN-shaped consumers, and none for FinChat, which has none. Sequenced
  behind the annotations because the annotations are the mapping such an adapter would
  be generated from — doing the adapter first would have meant hand-writing that mapping
  in code where nothing checks it.
- **Keep the mapping in a document.** A capability map in `docs/` with a table. It is
  what most banks have, and it is stale within a quarter, because nothing consumes it and
  nothing checks it. The bundle's rule since Inc 22 is that knowledge nothing reads is
  the artifact this project exists to avoid.
- **Align to a different standard (ISO 20022, FIBO).** ISO 20022 is a message standard
  and FinChat has no payment messages; FIBO is a formal OWL ontology of financial
  *instruments*, far richer than a retail deposits-and-loans model needs and without the
  capability-boundary rule that made BIAN the useful one here. Either could be added the
  same way — another `standards.*` block — if a consumer needed it.
