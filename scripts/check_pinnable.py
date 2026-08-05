#!/usr/bin/env python3
"""
Probe whether a pinnable model snapshot exists (ADR-0022).

`model_pins.PINNABLE` records, per model, whether the provider publishes a snapshot to
pin to. That is a fact about someone else's product: it was true on the date recorded and
will change without anyone telling us. This script re-derives it from the live API so the
table can be refreshed rather than trusted.

What it checks, per model:
  1. the publisher model resource — does it declare a versionId other than "default"?
  2. which id forms actually resolve — bare alias, @default, -001, @001

A model is "pinnable" when some id OTHER than the bare alias and @default resolves.
@default is not a pin: it is the alias with extra syntax, and it moves when default moves.

    python scripts/check_pinnable.py
    python scripts/check_pinnable.py --model gemini-2.5-flash --region us-central1

Exit 1 when the live result contradicts what model_pins.PINNABLE records, so this can be
run on a schedule and noticed.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_pins import ALIASES, PINNABLE, PINNABLE_VERIFIED  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = "strongsville-city-schools"


def _token() -> str:
    from google.auth import default
    from google.auth.transport.requests import Request
    creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token


def _get(url: str, token: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        # ADC needs an explicit quota project for aiplatform, or the call 403s with a
        # message that looks like a permissions problem and isn't.
        "x-goog-user-project": PROJECT,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def _resolves(model: str, region: str, token: str) -> int:
    """HTTP status for a minimal generateContent against this exact model id."""
    url = (f"https://{region}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
           f"/locations/{region}/publishers/google/models/{model}:generateContent")
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                       "generationConfig": {"maxOutputTokens": 1}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def probe(model: str, region: str, token: str) -> dict:
    base = model.split("@")[0]
    status, meta = _get(
        f"https://{region}-aiplatform.googleapis.com/v1beta1/publishers/google/models/{base}",
        token)
    version_id = meta.get("versionId")

    # @default is deliberately NOT counted as a pin — it is the alias with extra syntax.
    candidates = [base, f"{base}@default", f"{base}-001", f"{base}@001"]
    resolved = {c: _resolves(c, region, token) for c in candidates}
    pinnable = any(code == 200 for c, code in resolved.items()
                   if c not in (base, f"{base}@default"))

    return {"model": base, "versionId": version_id, "resolved": resolved,
            "pinnable": pinnable, "meta_status": status}


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe pinnable model snapshots.")
    ap.add_argument("--model", action="append", help="model id (repeatable)")
    ap.add_argument("--region", default="us-central1")
    args = ap.parse_args()

    models = args.model or sorted(set(ALIASES.values()))
    token = _token()
    mismatches = []

    print(f"Pinnable probe — {args.region}  (recorded {PINNABLE_VERIFIED})\n")
    for m in models:
        r = probe(m, args.region, token)
        recorded = PINNABLE.get(m)
        print(f"  {r['model']}")
        print(f"    versionId : {r['versionId']}")
        for cid, code in r["resolved"].items():
            mark = "ok " if code == 200 else "404" if code == 404 else str(code)
            print(f"    {mark}  {cid}")
        verdict = "PINNABLE" if r["pinnable"] else "no snapshot published"
        print(f"    -> {verdict}")
        if recorded is not None and recorded != r["pinnable"]:
            mismatches.append(f"{m}: recorded pinnable={recorded}, live={r['pinnable']}")
            print(f"    !! contradicts model_pins.PINNABLE (recorded {recorded})")
        print()

    if mismatches:
        print("MISMATCH — update model_pins.PINNABLE and PINNABLE_VERIFIED:")
        for m in mismatches:
            print(f"  {m}")
        print("\nIf a model became pinnable, set FINCHAT_PIN_* and the canary stops being")
        print("the only thing standing between you and a silent provider change.")
        return 1

    print("live API agrees with model_pins.PINNABLE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
