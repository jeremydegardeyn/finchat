"""Shared constants — Silver sink schema and pipeline version.

No Beam dependency, so every component (and the unit tests) imports it freely
without pulling a runner.
"""
PIPELINE_VERSION = "1.0.0"

SILVER_SCHEMA = {
    "fields": [
        {"name": "transaction_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "idempotency_key", "type": "STRING", "mode": "REQUIRED"},
        {"name": "account_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "txn_type", "type": "STRING", "mode": "REQUIRED"},
        {"name": "amount", "type": "NUMERIC", "mode": "REQUIRED"},
        {"name": "currency", "type": "STRING", "mode": "REQUIRED"},
        {"name": "counterparty_account", "type": "STRING", "mode": "NULLABLE"},
        {"name": "status", "type": "STRING", "mode": "REQUIRED"},
        {"name": "event_time", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "ingest_time", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "source_system", "type": "STRING", "mode": "NULLABLE"},
        {"name": "pipeline_version", "type": "STRING", "mode": "NULLABLE"},
    ]
}
