"""--account-ids: named accounts ride the same ingest path as random ones.

The demo ids (acct-001..003) are what docs/15, the SPA's fallback and the API's
DEMO_MODE all assume, but until this option existed the generator could only mint
UUIDs, so no real environment ever had them and an agent asked about acct-001 got a
404 from txn-api. The invariants the generator promises must survive pinning.
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

import generate

HERE = Path(__file__).resolve().parent
IDS = ["acct-001", "acct-002", "acct-003"]


def test_pinned_ids_are_the_only_accounts_emitted():
    rows = list(generate.generate(100, 4, 0.05, seed=1, account_ids=IDS))
    assert {r["account_id"] for r in rows} == set(IDS)


def test_per_customer_invariant_caps_the_run():
    rows = list(generate.generate(100, 4, 0.05, seed=1, account_ids=IDS))
    per = collections.Counter(r["account_id"] for r in rows)
    assert len(rows) == 12
    assert max(per.values()) <= 4 and min(per.values()) == 4


def test_count_below_cap_is_respected():
    rows = list(generate.generate(5, 4, 0.05, seed=1, account_ids=IDS))
    assert len(rows) == 5


def test_idempotency_key_carries_the_pinned_id():
    row = next(generate.generate(1, 4, 0.0, seed=1, account_ids=["acct-001"]))
    assert row["idempotency_key"].startswith("acct-001:")


def test_unpinned_behaviour_unchanged():
    rows = list(generate.generate(50, 4, 0.05, seed=7))
    per = collections.Counter(r["account_id"] for r in rows)
    assert len(rows) == 50 and max(per.values()) <= 4
    assert all(len(a) == 36 for a in per)  # still UUIDs


def test_cli_parses_and_rejects_duplicates():
    out = subprocess.run(
        [sys.executable, "generate.py", "--account-ids", "acct-001, acct-002", "--seed", "1", "--dry-run"],
        cwd=HERE, capture_output=True, text=True, check=True).stdout
    ids = {json.loads(l)["account_id"] for l in out.splitlines() if l.strip()}
    assert ids == {"acct-001", "acct-002"}
    dup = subprocess.run(
        [sys.executable, "generate.py", "--account-ids", "acct-001,acct-001", "--dry-run"],
        cwd=HERE, capture_output=True, text=True)
    assert dup.returncode != 0 and "repeat" in dup.stderr
