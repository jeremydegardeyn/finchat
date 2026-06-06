"""
Pure (Beam-free) transform logic for the transactions pipeline.

Kept separate from the Beam wiring so it can be unit-tested without a runner and
reused by other consumers. Implements schema enforcement, validation, and
enrichment. PII de-identification is applied in the Beam layer (DLP API call).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

PIPELINE_VERSION = "1.0.0"
VALID_TYPES = {"DEPOSIT", "WITHDRAWAL", "TRANSFER", "FEE"}
VALID_STATUS = {"POSTED", "PENDING", "REJECTED"}
REQUIRED = ("transaction_id", "idempotency_key", "account_id", "txn_type", "amount", "currency", "status", "event_time")
_AMOUNT_RE = re.compile(r"^[0-9]+(\.[0-9]{1,2})?$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")


class ValidationError(ValueError):
    """Raised when a message fails schema enforcement."""


def parse_and_validate(raw: bytes | str) -> dict:
    """Parse a raw Pub/Sub payload and enforce the transaction schema.

    Returns the validated record (amount cast to float for NUMERIC) or raises
    ValidationError. Callers route ValidationError to the DLQ.
    """
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValidationError(f"unparseable payload: {e}") from e

    if not isinstance(obj, dict):
        raise ValidationError("payload is not a JSON object")

    missing = [f for f in REQUIRED if f not in obj or obj[f] in (None, "")]
    if missing:
        raise ValidationError(f"missing required fields: {missing}")

    if obj["txn_type"] not in VALID_TYPES:
        raise ValidationError(f"invalid txn_type: {obj['txn_type']}")
    if obj["status"] not in VALID_STATUS:
        raise ValidationError(f"invalid status: {obj['status']}")
    if not _AMOUNT_RE.match(str(obj["amount"])):
        raise ValidationError(f"invalid amount format: {obj['amount']}")
    if not _CCY_RE.match(str(obj["currency"])):
        raise ValidationError(f"invalid currency: {obj['currency']}")
    try:
        datetime.fromisoformat(str(obj["event_time"]).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValidationError(f"invalid event_time: {obj['event_time']}") from e

    return {
        "transaction_id": obj["transaction_id"],
        "idempotency_key": obj["idempotency_key"],
        "account_id": obj["account_id"],
        "txn_type": obj["txn_type"],
        "amount": float(obj["amount"]),
        "currency": obj["currency"],
        "counterparty_account": obj.get("counterparty_account"),
        "status": obj["status"],
        "event_time": str(obj["event_time"]).replace("Z", "+00:00"),
    }


def enrich(record: dict, source_system: str = "synthetic-generator") -> dict:
    """Add lineage/provenance columns required by the Silver schema."""
    record = dict(record)
    record["ingest_time"] = datetime.now(timezone.utc).isoformat()
    record["source_system"] = source_system
    record["pipeline_version"] = PIPELINE_VERSION
    return record


def to_dlq_envelope(raw: bytes | str, error: str) -> bytes:
    """Wrap a failed message + reason for the dead-letter queue."""
    payload = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return json.dumps({
        "error": error,
        "payload": payload,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
    }).encode("utf-8")
