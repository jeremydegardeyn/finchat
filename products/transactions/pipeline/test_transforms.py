"""Unit tests for the pure components (validation + enrich) — no Beam runner needed."""
import json
import pytest
from finchat_pipeline.validation import parse_and_validate, to_dlq_envelope, ValidationError
from finchat_pipeline.enrich import enrich

VALID = {
    "transaction_id": "11111111-1111-1111-1111-111111111111",
    "idempotency_key": "acct:DEPOSIT:1700000000:100.00",
    "account_id": "acct-1",
    "txn_type": "DEPOSIT",
    "amount": "100.00",
    "currency": "USD",
    "counterparty_account": None,
    "status": "POSTED",
    "event_time": "2026-01-01T00:00:00+00:00",
}


def test_valid_message_parses():
    rec = parse_and_validate(json.dumps(VALID).encode())
    assert rec["amount"] == 100.0
    assert rec["txn_type"] == "DEPOSIT"


def test_enrich_adds_lineage():
    rec = enrich(parse_and_validate(json.dumps(VALID)))
    assert rec["pipeline_version"]
    assert rec["ingest_time"]
    assert rec["source_system"] == "synthetic-generator"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("amount"),
    lambda d: d.update(txn_type="BRIBE"),
    lambda d: d.update(status="HACKED"),
    lambda d: d.update(amount="12.345"),
    lambda d: d.update(currency="usd"),
    lambda d: d.update(event_time="not-a-date"),
])
def test_invalid_messages_rejected(mutate):
    bad = dict(VALID)
    mutate(bad)
    with pytest.raises(ValidationError):
        parse_and_validate(json.dumps(bad))


def test_unparseable_payload():
    with pytest.raises(ValidationError):
        parse_and_validate(b"\xff\xfenot json")


def test_dlq_envelope_roundtrip():
    env = json.loads(to_dlq_envelope(b"garbage", "boom"))
    assert env["error"] == "boom"
    assert env["payload"] == "garbage"
