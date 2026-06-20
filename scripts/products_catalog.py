#!/usr/bin/env python3
"""
Single source of truth for the FinChat data products.

Both catalog_bootstrap.py (Universal Catalog: glossary, aspects, DQ insights) and
data_products.py (Data Products API: products, assets, access groups) import this
so the metadata model can't drift between them. The per-product `contract` block
is also the summary attached as the `data-contract` aspect; the full contract is
authored as code in contracts/<id>.yaml.
"""
from __future__ import annotations

PROJECT = "strongsville-city-schools"
REGION = "us-central1"
STEWARD = "data-steward@datadinosaur.com"


def products(env: str) -> list[dict]:
    """Return the canonical product list for an environment."""
    return [
        {
            "id": "deposit-transactions", "display": "Deposit Transactions",
            "domain": "deposits", "dataset": f"finchat_silver_{env}", "table": "transaction",
            "owner": "deposits-product@datadinosaur.com", "steward": STEWARD,
            "criticality": "HIGH", "certification": "CERTIFIED", "pii": "PII_FINANCIAL",
            "sla": "freshness<=15m; 99.9% avail", "cost_center": "CC-DEPOSITS",
            "description": "Posted deposit/withdrawal/transfer/fee events on deposit "
                           "accounts (silver, de-identified, policy-tagged).",
            "contract": {
                "version": "1.2.0",
                "guarantees": "Append-only; idempotency_key unique; amount NUMERIC non-null; "
                              "txn_type in {DEPOSIT,WITHDRAWAL,TRANSFER,FEE}.",
                "freshness": "<=15m", "availability": "99.9%",
                "deprecation_policy": "90-day notice; n-1 schema supported.",
            },
            "access_groups": [
                {"id": "analysts", "display": "Deposit Analysts",
                 "group": "deposit-analysts@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Read deposit transactions for reporting & analytics."},
                {"id": "data-science", "display": "Data Science",
                 "group": "data-science@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Feature engineering and model training."},
            ],
        },
        {
            "id": "customer-master", "display": "Customer Master",
            "domain": "customer", "dataset": f"finchat_silver_{env}", "table": "customer",
            "owner": "customer-product@datadinosaur.com", "steward": STEWARD,
            "criticality": "CRITICAL", "certification": "CERTIFIED", "pii": "PII_DIRECT",
            "sla": "daily refresh; 99.95% avail", "cost_center": "CC-CUSTOMER",
            "description": "Authoritative single source of truth for customer identity & "
                           "demographics (silver). Direct PII behind policy tags.",
            "contract": {
                "version": "2.0.0",
                "guarantees": "customer_id PK stable; customer_natural_key (gov-ID hash) NK; "
                              "full_name/email masked unless fine-grained reader.",
                "freshness": "<=24h", "availability": "99.95%",
                "deprecation_policy": "180-day notice (critical master data).",
            },
            "access_groups": [
                {"id": "crm", "display": "CRM Team",
                 "group": "crm-team@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Customer relationship management (masked PII)."},
                {"id": "data-science", "display": "Data Science",
                 "group": "data-science@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Segmentation & modeling (masked PII)."},
            ],
        },
        {
            "id": "overdraft-history", "display": "Overdraft History",
            "domain": "risk", "dataset": f"finchat_gold_{env}", "table": "overdraft_history",
            "owner": "risk-product@datadinosaur.com", "steward": STEWARD,
            "criticality": "HIGH", "certification": "CERTIFIED", "pii": "PII_FINANCIAL",
            "sla": "daily; 99.9% avail", "cost_center": "CC-RISK",
            "description": "Negative-balance events aggregated for risk decisioning (gold).",
            "contract": {
                "version": "1.0.1",
                "guarantees": "One aggregated row per account; overdraft_ratio in [0,1]; "
                              "derived from certified silver transactions.",
                "freshness": "<=24h", "availability": "99.9%",
                "deprecation_policy": "90-day notice.",
            },
            "access_groups": [
                {"id": "risk-analysts", "display": "Risk Analysts",
                 "group": "risk-analysts@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Credit & liquidity risk analysis."},
                {"id": "collections", "display": "Collections",
                 "group": "collections-team@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Overdraft collections workflows."},
            ],
        },
        {
            "id": "loan-master", "display": "Loan Master",
            "domain": "lending", "dataset": f"finchat_loans_{env}", "table": "loan_status",
            "owner": "lending-product@datadinosaur.com", "steward": STEWARD,
            "criticality": "HIGH", "certification": "CANDIDATE", "pii": "PII_FINANCIAL",
            "sla": "near-real-time; 99.9% avail", "cost_center": "CC-LENDING",
            "description": "Loan applications, risk scores and current approval status "
                           "(lending product; append-only decision lineage).",
            "contract": {
                "version": "0.9.0",
                "guarantees": "loan_id PK; status reflects latest decision; full audit trail "
                              "in loan_audit_log; risk_score 0-1000.",
                "freshness": "near-real-time", "availability": "99.9%",
                "deprecation_policy": "Candidate product — contract not yet frozen.",
            },
            "access_groups": [
                {"id": "underwriting", "display": "Underwriting",
                 "group": "underwriting-team@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Loan underwriting & decisioning."},
                {"id": "risk-analysts", "display": "Risk Analysts",
                 "group": "risk-analysts@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Credit-exposure analysis."},
            ],
        },
        {
            "id": "bank-knowledge-base", "display": "Bank Knowledge Base",
            "domain": "marketing", "dataset": f"finchat_kb_{env}", "table": "kb_chunks",
            "owner": "ai-platform@datadinosaur.com", "steward": STEWARD,
            "criticality": "MEDIUM", "certification": "CERTIFIED", "pii": "PUBLIC",
            "sla": "on-publish; 99.5% avail", "cost_center": "CC-AI",
            "description": "Embedded policy/product documents grounding the FinChat agent "
                           "(RAG corpus with ML.GENERATE_EMBEDDING vectors).",
            "contract": {
                "version": "1.1.0",
                "guarantees": "doc_id unique; embedding dim=768 (text-embedding); "
                              "title + category retained for citation.",
                "freshness": "on-publish", "availability": "99.5%",
                "deprecation_policy": "30-day notice; re-embed on model change.",
            },
            "access_groups": [
                {"id": "ai-platform", "display": "AI Platform",
                 "group": "ai-platform@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Agent grounding & RAG retrieval."},
                {"id": "support", "display": "Support Agents",
                 "group": "support-agents@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Customer-support knowledge lookup."},
            ],
        },
        {
            "id": "customer-360-analytics", "display": "Customer 360 Analytics",
            "domain": "analytics", "dataset": f"finchat_graph_{env}", "table": "customer_360",
            "owner": "analytics-product@datadinosaur.com", "steward": STEWARD,
            "criticality": "HIGH", "certification": "CANDIDATE", "pii": "PII_FINANCIAL",
            "sla": "<=30m rollup; 99.9% avail", "cost_center": "CC-ANALYTICS",
            "description": "Curated cross-domain analytics surface powering Conversational "
                           "Analytics (customer_360 + analyst dim/fact views). Bundles the "
                           "source tables the CA planner resolves, so an approved analyst gets "
                           "every read CA needs in one grant; direct & financial PII stay masked "
                           "unless fine-grained reader.",
            "contract": {
                "version": "0.1.0",
                "guarantees": "Derived rollup; one row per customer_id; financial + direct PII "
                              "masked (NULL) for the analyst tier via policy-tag data policies.",
                "freshness": "<=30m", "availability": "99.9%",
                "deprecation_policy": "Candidate product — contract not yet frozen.",
            },
            # Cross-domain: bundles the physical tables CA's planner resolves (not just the
            # customer_360 view), so one approval confers every read CA needs. These assets
            # overlap other products by design (e.g. customer is also Customer Master).
            "assets": [
                {"dataset": f"finchat_silver_{env}", "table": "customer"},
                {"dataset": f"finchat_silver_{env}", "table": "account"},
                {"dataset": f"finchat_silver_{env}", "table": "transaction"},
                {"dataset": f"finchat_gold_{env}",   "table": "overdraft_history"},
                {"dataset": f"finchat_loans_{env}",  "table": "loan_request"},
                {"dataset": f"finchat_graph_{env}",  "table": "customer_360"},
            ],
            # Cross-domain grant: route approval through each source domain owner + analytics,
            # so no single approver unilaterally grants broad read across four domains.
            "approvers": [
                "analytics-product@datadinosaur.com",
                "customer-product@datadinosaur.com",
                "deposits-product@datadinosaur.com",
                "risk-product@datadinosaur.com",
                "lending-product@datadinosaur.com",
            ],
            "access_groups": [
                {"id": "analysts", "display": "Data Science / Analysts",
                 "group": "data-science@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Conversational Analytics over the semantic layer (masked PII)."},
            ],
        },
        {
            "id": "reconciliation-steward", "display": "Reconciliation Steward",
            "domain": "governance", "dataset": f"finchat_steward_{env}", "table": "steward_status",
            "owner": "data-governance@datadinosaur.com", "steward": STEWARD,
            "criticality": "MEDIUM", "certification": "CANDIDATE", "pii": "PUBLIC",
            "sla": "nightly; 99.5% avail", "cost_center": "CC-GOVERNANCE",
            "description": "Outputs of the durable reconciliation / data-quality steward "
                           "(Inc 19 / ADR-0021): per-step evaluator decisions and the "
                           "append-only audit of nightly checks against the data contracts. "
                           "Metadata about data quality — carries no customer PII.",
            "contract": {
                "version": "0.1.0",
                "guarantees": "Append-only steward_decision (INSERT-only, reconstructable); "
                              "eval_score in [0,1]; escalations carry the verified approver; "
                              "one durable run per nightly window.",
                "freshness": "nightly", "availability": "99.5%",
                "deprecation_policy": "Candidate product — contract not yet frozen.",
            },
            "access_groups": [
                {"id": "data-governance", "display": "Data Governance",
                 "group": "data-governance@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Monitor data-quality reconciliation runs and the audit trail."},
                {"id": "risk-analysts", "display": "Risk Analysts",
                 "group": "risk-analysts@datadinosaur.com", "roles": ["roles/bigquery.dataViewer"],
                 "desc": "Review flagged reconciliation anomalies."},
            ],
        },
    ]


def fqn(p: dict) -> str:
    """Dataplex catalog fully-qualified name for a product's BQ table."""
    return f"bigquery:{PROJECT}.{p['dataset']}.{p['table']}"
