# 15 — Example conversations (Customer & Analyst)

Concrete, working chats for the two conversational personas. Open the app, pick the
persona, and try these. Each persona has a **different grounding** and a **different
audience** — the customer agent never sees the catalog or cross-customer data; the
analyst never sees another customer's raw PII.

| Persona | Chat surface | Grounding | Runtime |
|---|---|---|---|
| **Customer** | Banking Assistant | own account (DaaS API) + Knowledge Base (RAG) | ADK + Gemini agent (Cloud Run) |
| **Employee (Analyst)** | Conversational Analytics + Knowledge Base + Catalog search | all data products via the **knowledge graph**; KB via RAG | Gemini Data Analytics + Dataplex |

---

## 1. Customer — Banking Assistant

Grounded **only** in the signed-in customer's account (via the Transactions DaaS API)
and the bank Knowledge Base (RAG). It will ask for an account id if needed, refuses
investment advice, and never reveals other customers' data.

**Account questions** (tool: account balance / transactions / summary)
```
You:  what's my balance?
Bot:  Your balance on acct-001 is -2,972.49 USD.

You:  show my recent transactions
Bot:  Recent activity on acct-001: DEPOSIT 486.03, TRANSFER 974.33, FEE 35.00,
      WITHDRAWAL 1,200.00 (most recent first).

You:  give me an account summary
Bot:  acct-001: 6 deposits, 9 withdrawals, 2 fees; net balance -2,972.49 USD.
```

**Knowledge Base questions** (tool: `search_knowledge_base` → BigQuery `VECTOR_SEARCH`)
```
You:  what are the overdraft fees?
Bot:  FinChat Bank charges a $35 overdraft fee per item, capped at 3 per day…
      (grounded in the Fees policy document)

You:  when is the Lakewood branch open?
Bot:  The Lakewood branch is open Mon–Fri 9:00–17:00 and Sat 9:00–13:00…
```

**Loan status** (tool: `get_loan_status`) + **explainability**
```
You:  what's the status of loan loan-abc123?
Bot:  Loan loan-abc123 is PENDING_APPROVAL. Risk score 50 → REVIEW.
      Principal reasons: poor credit (548); large loan amount.
```
> The full **"Why this decision"** factor scorecard (each factor's point contribution)
> renders under the loan submission — see [ADR-0013](adr/0013-loan-decision-explainability.md).

**Guardrails**
```
You:  should I buy Tesla stock?
Bot:  I can't provide investment advice, but I can share your account data.
```
(Prompts/responses are also screened by **Model Armor** at the BFF.)

---

## 2. Employee (Analyst)

Two surfaces in the Analyst view: **Catalog discovery** (search box) and a single unified
**"Ask the Data"** assistant that **auto-routes** each question — the BFF classifies intent
(Gemini, with a keyword-heuristic fallback) and dispatches to **📊 Analytics** (Conversational
Analytics over the data products) or **📚 Knowledge Base** (RAG). Each answer shows a badge for
the tool it used. No toggle, no per-account customer tools — the analyst works at population
scale through the graph.

### 2a. Auto-routed → Analytics (Gemini Data Analytics, graph-grounded)

Returns a natural-language answer **plus the generated SQL** (transparency) and the result
table. Joins are correct because the [knowledge graph](14-knowledge-graph.md) join keys are
passed as a system instruction.

```
You:  total deposits and transaction counts by customer segment?
CA:   PREMIER leads with ~$3.78M deposits (7,836 txns), then STUDENT ~$3.57M…
      [Generated SQL] joins transaction → account → customer, groups by segment
      [Result] 4 rows: PREMIER / BUSINESS / STUDENT / RETAIL
```
```
You:  which customers have the most overdrafts?
CA:   Uses customer_360 (pre-joined) — returns the top customer_ids by
      overdraft_events with their segment.
```
```
You:  how many customers per segment, and average accounts each?
CA:   PREMIER 1,959 · BUSINESS 1,865 · STUDENT 1,851 · RETAIL 1,825 …
```
```
You:  monthly fee revenue this year
CA:   Drills into the raw transaction table (txn_type='FEE') — grouped by month.
      (Granular question the customer_360 rollup doesn't pre-aggregate.)
```
> **Why these work:** per-customer questions use the pre-joined `customer_360`; granular
> ones use the raw entities with the join keys from the system instruction. The analyst
> never needs to know table names or join columns.

### 2b. Auto-routed → Knowledge Base (RAG)

Same chat box — questions about policies/fees/docs are routed to the **📚 Knowledge Base**
(VECTOR_SEARCH corpus, same as the customer KB), framed for staff.
```
You:  what's our overdraft fee policy?
KB:   $35 per item, max 3/day… (cited to the Fees policy doc)

You:  summarize the privacy policy
KB:   …grounded only in retrieved kb_chunks; says so if not covered.
```

### 2c. Knowledge Catalog — discover assets by description

Free-text search over the Dataplex Universal Catalog (FinChat assets only). Returns
governed metadata (domain, owner, certification, PII class, contract version, DQ).

| Type this | Returns |
|---|---|
| `overdraft history` | Overdraft History — domain risk · CERTIFIED · PII_FINANCIAL · contract v1.0.1 · DQ |
| `deposit transactions` | Deposit Transactions |
| `customer master` / `authoritative customer record` | Customer Master |
| `knowledge base` | Bank Knowledge Base |
| `lending` | Loan Master |
| `credit exposure`, `fraud transaction history` | glossary concepts → linked products |

> Use the **two-word concept/product phrase**; a single generic word also matches column
> names in other tables.

---

## 3. Persona boundaries (why the split matters)

- **Customer** sees only their own account + public KB. No catalog, no cross-customer data,
  no SQL. Direct PII is theirs alone; everyone else's is masked by CLS.
- **Analyst** sees aggregate analytics + catalog + KB, but `customer_360`/graph expose
  `customer_id` + `segment` only — **never** names/emails (CLS + view design).
- Both conversational surfaces are governed: agent answers are tool-grounded and
  Model-Armor-screened; analyst answers show their SQL for auditability.
