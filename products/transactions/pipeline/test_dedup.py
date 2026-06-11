"""End-to-end DirectRunner test for the in-stream idempotency dedup stage.

Runs the REAL pipeline (build_pipeline via run()) in file mode: duplicate
idempotency_keys in -> exactly one row per key out. Requires apache-beam, so it
is skipped where Beam isn't installed (the CI fast job runs test_transforms.py;
run this one locally: pytest test_dedup.py -q).
"""
import json

import pytest

pytest.importorskip("apache_beam")


def _row(txn_id: str, key: str) -> dict:
    return {
        "transaction_id": txn_id,
        "idempotency_key": key,
        "account_id": "acct-1",
        "txn_type": "DEPOSIT",
        "amount": "10.00",
        "currency": "USD",
        "status": "POSTED",
        "event_time": "2026-01-01T00:00:00+00:00",
    }


def test_pipeline_collapses_duplicate_keys(tmp_path):
    import pipeline as pl

    rows = [
        _row("t1", "k1"),
        _row("t1", "k1"),                # publisher retry (same event republished)
        _row("t1-redeliver", "k1"),      # Pub/Sub redelivery w/ different envelope
        _row("t2", "k2"),
    ]
    inp = tmp_path / "in.jsonl"
    inp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "out"

    pl.run([
        "--input_file", str(inp),
        "--output_file", str(out),
        "--dedup_ttl_seconds", "3600",
        "--dlp_sample_rate", "0",
    ])

    lines = []
    for f in tmp_path.glob("out*.jsonl"):
        lines += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    keys = sorted(r["idempotency_key"] for r in lines)
    assert keys == ["k1", "k2"], f"expected one row per key, got {keys}"


def test_dedup_disabled_passes_duplicates_through(tmp_path):
    import pipeline as pl

    rows = [_row("t1", "k1"), _row("t1", "k1")]
    inp = tmp_path / "in.jsonl"
    inp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "out"

    pl.run(["--input_file", str(inp), "--output_file", str(out),
            "--dedup_ttl_seconds", "0", "--dlp_sample_rate", "0"])

    lines = []
    for f in tmp_path.glob("out*.jsonl"):
        lines += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2  # toggle off -> append-only behavior preserved
