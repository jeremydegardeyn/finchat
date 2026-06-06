#!/usr/bin/env python3
"""
FinChat synthetic transaction generator.

Produces realistic retail-banking transactions and publishes them to Pub/Sub
(or to a local file / stdout for offline runs).

Requirements honored:
  * up to 10,000 transactions per run            (--count, hard-capped at 10000)
  * <= 4 transactions per customer per execution (--max-per-customer, default 4)
  * configurable volume                          (--count)
  * deposits, withdrawals, transfers, fees       (realistic mix + patterns)
  * seeded overdraft sequences                   (--overdraft-rate) for the loan product

Examples:
  # Offline: write JSON lines, no GCP needed
  python generate.py --count 200 --out sample.jsonl

  # Publish to Pub/Sub
  python generate.py --count 5000 --project strongsville-city-schools \\
      --topic finchat-dev-transactions-ingest
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

MAX_TXNS = 10_000
CURRENCIES = ["USD"]
SEGMENTS = ["RETAIL", "PREMIER", "STUDENT", "BUSINESS"]
ACCOUNT_TYPES = ["CHECKING", "SAVINGS"]
TXN_TYPES = ["DEPOSIT", "WITHDRAWAL", "TRANSFER", "FEE"]
# Realistic weighting: most activity is deposits/withdrawals.
TXN_WEIGHTS = [0.40, 0.40, 0.12, 0.08]


def money(value: float) -> str:
    """Round to 2dp and return as string to preserve NUMERIC precision over the wire."""
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class Account:
    account_id: str
    account_number: str
    customer_id: str
    account_type: str
    currency: str


def build_customers(n_customers: int, rng: random.Random) -> list[Account]:
    """One primary account per synthetic customer (sufficient for demo volume)."""
    accounts: list[Account] = []
    for _ in range(n_customers):
        customer_id = str(uuid.uuid4())
        accounts.append(
            Account(
                account_id=str(uuid.uuid4()),
                account_number=f"{rng.randint(10**9, 10**10 - 1)}",
                customer_id=customer_id,
                account_type=rng.choice(ACCOUNT_TYPES),
                currency=rng.choice(CURRENCIES),
            )
        )
    return accounts


def realistic_amount(txn_type: str, rng: random.Random) -> float:
    """Amount distributions that look like real retail banking behavior."""
    if txn_type == "DEPOSIT":
        # payroll-ish clusters + occasional large deposits
        return rng.choice([rng.uniform(50, 400), rng.uniform(1200, 3500)])
    if txn_type == "WITHDRAWAL":
        return rng.uniform(10, 600)
    if txn_type == "TRANSFER":
        return rng.uniform(25, 1500)
    if txn_type == "FEE":
        return rng.choice([5, 12, 25, 35])  # NSF/maintenance fees
    return rng.uniform(1, 100)


def make_transaction(
    acct: Account,
    rng: random.Random,
    base_time: datetime,
    force_overdraft: bool,
) -> dict:
    if force_overdraft:
        # Large withdrawal to push balance negative (seeds overdraft_history).
        txn_type = "WITHDRAWAL"
        amount = rng.uniform(800, 2500)
    else:
        txn_type = rng.choices(TXN_TYPES, weights=TXN_WEIGHTS, k=1)[0]
        amount = realistic_amount(txn_type, rng)

    event_time = base_time - timedelta(
        days=rng.randint(0, 90), seconds=rng.randint(0, 86_400)
    )
    counterparty = None
    if txn_type == "TRANSFER":
        counterparty = f"{rng.randint(10**9, 10**10 - 1)}"

    # 3% rejected to exercise status handling / RLS filter.
    status = "REJECTED" if rng.random() < 0.03 else "POSTED"

    return {
        "transaction_id": str(uuid.uuid4()),
        # Natural key for idempotent MERGE: stable per (account, type, time, amount).
        "idempotency_key": f"{acct.account_id}:{txn_type}:{int(event_time.timestamp())}:{money(amount)}",
        "account_id": acct.account_id,
        "txn_type": txn_type,
        "amount": money(amount),
        "currency": acct.currency,
        "counterparty_account": counterparty,
        "status": status,
        "event_time": event_time.astimezone(timezone.utc).isoformat(),
    }


def generate(count: int, max_per_customer: int, overdraft_rate: float, seed: int | None):
    rng = random.Random(seed)
    count = min(count, MAX_TXNS)
    # Need enough customers so we never exceed max_per_customer.
    n_customers = max(1, -(-count // max_per_customer))  # ceil
    accounts = build_customers(n_customers, rng)
    base_time = datetime.now(timezone.utc)

    # Round-robin assignment guarantees the <= max_per_customer invariant.
    per_customer_count: dict[str, int] = {}
    emitted = 0
    idx = 0
    while emitted < count:
        acct = accounts[idx % len(accounts)]
        idx += 1
        if per_customer_count.get(acct.account_id, 0) >= max_per_customer:
            continue
        force = rng.random() < overdraft_rate
        yield make_transaction(acct, rng, base_time, force)
        per_customer_count[acct.account_id] = per_customer_count.get(acct.account_id, 0) + 1
        emitted += 1


def publish_to_pubsub(project: str, topic: str, messages, batch_log: int = 1000):
    from google.cloud import pubsub_v1  # imported lazily so offline runs need no deps

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, topic)
    futures = []
    sent = 0
    for msg in messages:
        data = json.dumps(msg).encode("utf-8")
        futures.append(publisher.publish(topic_path, data, source="finchat-generator"))
        sent += 1
        if sent % batch_log == 0:
            print(f"  published {sent}...", file=sys.stderr)
    for f in futures:
        f.result()  # block until all acked
    return sent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="FinChat synthetic transaction generator")
    p.add_argument("--count", type=int, default=1000, help=f"transactions to generate (max {MAX_TXNS})")
    p.add_argument("--max-per-customer", type=int, default=4, help="max transactions per customer per run")
    p.add_argument("--overdraft-rate", type=float, default=0.05, help="fraction of txns that force an overdraft")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
    p.add_argument("--project", help="GCP project (required to publish)")
    p.add_argument("--topic", help="Pub/Sub topic id (required to publish)")
    p.add_argument("--out", help="write JSON lines to this file instead of publishing")
    p.add_argument("--dry-run", action="store_true", help="print to stdout instead of publishing")
    args = p.parse_args(argv)

    if args.count > MAX_TXNS:
        print(f"warning: count capped at {MAX_TXNS}", file=sys.stderr)
    if args.max_per_customer < 1 or args.max_per_customer > 4:
        p.error("--max-per-customer must be between 1 and 4")

    messages = generate(args.count, args.max_per_customer, args.overdraft_rate, args.seed)

    if args.out:
        n = 0
        with open(args.out, "w", encoding="utf-8") as fh:
            for m in messages:
                fh.write(json.dumps(m) + "\n")
                n += 1
        print(f"wrote {n} transactions -> {args.out}")
    elif args.dry_run or not (args.project and args.topic):
        n = 0
        for m in messages:
            print(json.dumps(m))
            n += 1
        if not args.dry_run and not (args.project and args.topic):
            print(f"\n(no --project/--topic given; printed {n} txns. Use --dry-run to silence this note.)", file=sys.stderr)
    else:
        print(f"publishing {min(args.count, MAX_TXNS)} transactions to {args.topic}...", file=sys.stderr)
        sent = publish_to_pubsub(args.project, args.topic, messages)
        print(f"published {sent} transactions to projects/{args.project}/topics/{args.topic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
