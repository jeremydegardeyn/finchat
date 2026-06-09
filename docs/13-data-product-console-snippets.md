# 13 — Data Product console snippets (Contract & Insights tabs)

The Dataplex **Data Products** page has two tabs that are **preview / console-only**
(Google's gated `contract` + `query-recommendations` system aspect types — no public
API, see [docs/12 §9](12-knowledge-catalog.md)). Everything else (Aspects, Overview,
access groups, assets) is automated. Use this page to fill those two tabs by hand in
~30 seconds each.

For each product, open it on the
[Data Products page](https://console.cloud.google.com/dataplex/catalog/data-products?project=strongsville-city-schools):

- **Contract tab → "+ Add"** — paste the *Contract guarantees* blurb.
- **Insights tab → "Query recommendations" → "Edit"** — add the *Sample queries*.

Queries are runnable as-is against **prod** (swap `_prod` for `_dev`/`_test`).
Full contracts are code in [`contracts/`](../contracts/).

---

## 1. Deposit Transactions  ·  `finchat_silver_prod.transaction`

**Contract guarantees:** Append-only posted deposit/withdrawal/transfer/fee events.
`transaction_id` PK; `idempotency_key` unique (MERGE dedup); `amount` NUMERIC non-null;
`txn_type ∈ {DEPOSIT,WITHDRAWAL,TRANSFER,FEE}`. **Freshness ≤15m · availability 99.9%.**
`amount`/`counterparty_account` masked unless fine-grained reader. v1.2.0 ACTIVE ·
owner deposits-product@datadinosaur.com · 90-day deprecation notice.

**Sample queries:**
```sql
-- Daily volume by transaction type
SELECT DATE(event_time) AS day, txn_type, COUNT(*) AS txns, SUM(amount) AS total
FROM `strongsville-city-schools.finchat_silver_prod.transaction`
GROUP BY day, txn_type
ORDER BY day DESC, txn_type;
```
```sql
-- Top 20 most active accounts
SELECT account_id, COUNT(*) AS txns, SUM(amount) AS total_amount
FROM `strongsville-city-schools.finchat_silver_prod.transaction`
GROUP BY account_id
ORDER BY txns DESC
LIMIT 20;
```
```sql
-- Monthly fee revenue
SELECT DATE_TRUNC(DATE(event_time), MONTH) AS month, SUM(amount) AS fee_revenue
FROM `strongsville-city-schools.finchat_silver_prod.transaction`
WHERE txn_type = 'FEE'
GROUP BY month
ORDER BY month;
```

---

## 2. Customer Master  ·  `finchat_silver_prod.customer`

**Contract guarantees:** Authoritative single source of truth for customer identity.
`customer_id` PK (stable, never reused); `customer_natural_key` (gov-ID hash) NK;
`full_name`/`email` are **PII_DIRECT** and masked unless fine-grained reader.
**Freshness ≤24h · availability 99.95%.** v2.0.0 ACTIVE · owner
customer-product@datadinosaur.com · 180-day deprecation notice (critical master data).

**Sample queries:**
```sql
-- Customer distribution by segment
SELECT segment, COUNT(*) AS customers
FROM `strongsville-city-schools.finchat_silver_prod.customer`
GROUP BY segment
ORDER BY customers DESC;
```
```sql
-- New customers per month
SELECT DATE_TRUNC(DATE(created_at), MONTH) AS month, COUNT(*) AS new_customers
FROM `strongsville-city-schools.finchat_silver_prod.customer`
GROUP BY month
ORDER BY month;
```

---

## 3. Overdraft History  ·  `finchat_gold_prod.overdraft_history`

**Contract guarantees:** One aggregated row per account, derived only from CERTIFIED
silver transactions. `account_id` PK; `overdraft_ratio ∈ [0,1]`; `lowest_balance` is
the minimum observed balance (**PII_FINANCIAL**). **Freshness ≤24h · availability 99.9%.**
v1.0.1 ACTIVE · owner risk-product@datadinosaur.com · 90-day deprecation notice.

**Sample queries:**
```sql
-- Highest-risk overdraft accounts
SELECT account_id, overdraft_events, lowest_balance, overdraft_ratio
FROM `strongsville-city-schools.finchat_gold_prod.overdraft_history`
ORDER BY overdraft_events DESC, lowest_balance ASC
LIMIT 20;
```
```sql
-- Portfolio overdraft summary
SELECT COUNT(*) AS accounts,
       SUM(overdraft_events) AS total_events,
       ROUND(AVG(overdraft_ratio), 3) AS avg_ratio,
       MIN(lowest_balance) AS worst_balance
FROM `strongsville-city-schools.finchat_gold_prod.overdraft_history`;
```

---

## 4. Loan Master  ·  `finchat_loans_prod.loan_status`

**Contract guarantees:** `loan_status` projects the latest decision over an append-only
audit trail. `loan_id` PK; `status` reflects the most recent decision; `risk_score ∈
[0,1000]`; `amount`/`counteroffer_amount` are **PII_FINANCIAL**. **Near-real-time ·
availability 99.9%.** v0.9.0 **CANDIDATE** (contract not yet frozen) · owner
lending-product@datadinosaur.com.

**Sample queries:**
```sql
-- Decision breakdown (book size by outcome)
SELECT COALESCE(final_decision, status) AS outcome,
       COUNT(*) AS loans, SUM(amount) AS amount
FROM `strongsville-city-schools.finchat_loans_prod.loan_status`
GROUP BY outcome
ORDER BY loans DESC;
```
```sql
-- Average risk score by status
SELECT status, COUNT(*) AS loans, ROUND(AVG(risk_score), 1) AS avg_risk
FROM `strongsville-city-schools.finchat_loans_prod.loan_status`
GROUP BY status
ORDER BY loans DESC;
```
```sql
-- Approved-book exposure
SELECT SUM(amount) AS approved_exposure, COUNT(*) AS approved_loans
FROM `strongsville-city-schools.finchat_loans_prod.loan_status`
WHERE final_decision = 'APPROVE';
```

---

## 5. Bank Knowledge Base  ·  `finchat_kb_prod.kb_chunks`

**Contract guarantees:** Chunked, embedded policy/product docs grounding the agent.
`doc_id` PK; `embedding` is a 768-dim vector (ML.GENERATE_EMBEDDING); `title`+`category`
retained for citation. Retrieved via `VECTOR_SEARCH` for RAG. **On-publish · availability
99.5%.** v1.1.0 ACTIVE · owner ai-platform@datadinosaur.com · re-embed on model change.

**Sample queries:**
```sql
-- Knowledge coverage by category
SELECT category, COUNT(*) AS chunks
FROM `strongsville-city-schools.finchat_kb_prod.kb_chunks`
GROUP BY category
ORDER BY chunks DESC;
```
```sql
-- Document catalog
SELECT doc_id, title, category, LENGTH(content) AS chars
FROM `strongsville-city-schools.finchat_kb_prod.kb_chunks`
ORDER BY title;
```
```sql
-- Semantic (RAG) retrieval example — set <EMBEDDING_MODEL> to your kb embedding model
SELECT base.doc_id, base.title, base.category, distance
FROM VECTOR_SEARCH(
  TABLE `strongsville-city-schools.finchat_kb_prod.kb_chunks`, 'embedding',
  (SELECT ml_generate_embedding_result AS embedding
   FROM ML.GENERATE_EMBEDDING(
     MODEL `strongsville-city-schools.finchat_kb_prod.<EMBEDDING_MODEL>`,
     (SELECT 'what are the overdraft fees?' AS content))),
  top_k => 5);
```
