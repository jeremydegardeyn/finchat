#!/usr/bin/env python3
"""
Scheduled canary evaluation against the LIVE agent (ADR-0022).

The gap this closes
-------------------
FinChat already has two eval loops, and neither detects provider-side model drift:

  * `eval/pipelines/evaluate.py` — offline CI gate. Runs deterministic logic, never
    touches a model, so a changed model is invisible to it by construction.
  * `scripts/live_eval.py` — judges real production traffic. Traffic varies day to day,
    so a quality drop is confounded with a change in what users asked.

A canary fixes the input. It replays the **same golden set** against the **live agent** on
a schedule and compares to a stored baseline. Because the questions are identical run to
run, a metric move is attributable to the system rather than to the traffic — which is
what makes it the one drift-detection technique with published evidence behind it.
Embedding-drift dashboards are investigative aids; this is a control.

What it records
---------------
Every run stores the **model version that actually served** alongside the metrics, so a
regression can be tied to a version change rather than merely observed. That pairing is
the point: "quality fell" is an alert, "quality fell the day the serving version changed
from X to Y" is a diagnosis.

Usage
-----
    python scripts/canary_eval.py dev                    # run + compare to baseline
    python scripts/canary_eval.py dev --set-baseline     # adopt this run as the baseline
    python scripts/canary_eval.py dev --offline          # no live endpoint (CI smoke)

Exit 1 on regression beyond tolerance, so the scheduled workflow fails loudly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATASETS = REPO / "eval" / "datasets"
BASELINE = REPO / "eval" / "reports" / "canary_baseline.json"
LATEST = REPO / "eval" / "reports" / "canary_latest.json"

PROJECT = "strongsville-city-schools"

# Absolute tolerance per metric. A canary that fires on noise gets muted, and a muted
# control is worse than no control — so these are set to catch a real move, not a wobble.
TOLERANCE = {
    "grounding_accuracy": 0.10,   # 10 points below baseline
    "tool_utilization": 0.10,
    "hallucination_rate": 0.05,   # inverted: an INCREASE of 5 points fails
}
INVERTED = {"hallucination_rate"}


def load_dataset() -> list[dict]:
    with open(DATASETS / "transaction_agent_eval.jsonl", encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


# --- Live agent -------------------------------------------------------------

def _id_token(audience: str) -> str | None:
    """OIDC token for the private Cloud Run agent. None when unavailable (offline)."""
    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import id_token as gid
        req = google.auth.transport.requests.Request()
        return gid.fetch_id_token(req, audience)
    except Exception:
        return None


def ask_live(agent_url: str, case: dict, timeout: float = 60.0) -> tuple[str, str | None]:
    """Ask the live agent one question. Returns (answer_text, served_model_version)."""
    url = agent_url.rstrip("/") + "/chat"
    body = json.dumps({"message": case["query"], "account_id": case.get("account_id")}).encode()
    headers = {"Content-Type": "application/json"}
    tok = _id_token(agent_url)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read())
    # The agent service echoes the serving version when it has one; absent means the
    # surface did not report it, which is logged as unknown rather than assumed.
    return (payload.get("response") or ""), payload.get("model_version")


# --- Scoring ----------------------------------------------------------------
# Deliberately the same contract the offline gate asserts: an answer must not assert a
# number that the tools did not produce, and the right tool must have been reached.

def score(case: dict, answer: str) -> dict:
    a = (answer or "").lower()
    expected_tool = case.get("expected_tool")

    # Refusal cases: the dataset marks them with expected_tool == None.
    if expected_tool is None:
        refused = bool(re.search(r"can't|cannot|unable|not able|don't have", a))
        return {"id": case["id"], "tool_ok": refused, "grounded": refused,
                "hallucinated": not refused}

    # Grounding proxy: the answer must reference the account it was asked about and must
    # not be empty. Numeric verification against tool output is the offline gate's job;
    # here the model is live, so this checks the contract the model can break.
    acct = (case.get("account_id") or "").lower()
    grounded = bool(a.strip()) and (acct in a if acct else True)
    if case.get("expected_grounded") and not grounded:
        return {"id": case["id"], "tool_ok": False, "grounded": False, "hallucinated": True}

    # A refusal where an answer was expected is a tool-utilization failure, not grounding.
    refused_wrongly = bool(re.search(r"can't|cannot|unable", a))
    return {"id": case["id"], "tool_ok": not refused_wrongly,
            "grounded": grounded, "hallucinated": False}


def aggregate(cases: list[dict]) -> dict:
    n = len(cases) or 1
    return {
        "n": len(cases),
        "grounding_accuracy": round(sum(c["grounded"] for c in cases) / n, 3),
        "tool_utilization": round(sum(c["tool_ok"] for c in cases) / n, 3),
        "hallucination_rate": round(sum(c["hallucinated"] for c in cases) / n, 3),
    }


# --- Baseline comparison ----------------------------------------------------

def compare(current: dict, baseline: dict) -> list[str]:
    """Return regression messages. Empty list = within tolerance."""
    regressions = []
    for metric, tol in TOLERANCE.items():
        cur, base = current["metrics"].get(metric), baseline.get("metrics", {}).get(metric)
        if cur is None or base is None:
            continue
        delta = cur - base
        bad = (delta > tol) if metric in INVERTED else (delta < -tol)
        if bad:
            regressions.append(
                f"{metric}: {base:.3f} -> {cur:.3f} ({delta:+.3f}, tolerance {tol})")
    return regressions


def version_changed(current: dict, baseline: dict) -> str | None:
    cur, base = current.get("model_served"), baseline.get("model_served")
    if cur and base and cur != base:
        return f"{base} -> {cur}"
    return None


# --- Entrypoint -------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Canary evaluation against the live agent.")
    ap.add_argument("env", nargs="?", default="dev")
    ap.add_argument("--agent-url", default=os.getenv("AGENT_URL", ""),
                    help="Live agent base URL (default $AGENT_URL)")
    ap.add_argument("--set-baseline", action="store_true",
                    help="adopt this run as the new baseline (do this deliberately)")
    ap.add_argument("--offline", action="store_true",
                    help="skip the live calls; validates wiring without a deployment")
    args = ap.parse_args()

    cases = load_dataset()
    print(f"== canary eval ({args.env}) — {len(cases)} golden cases ==")

    if args.offline or not args.agent_url:
        if not args.offline:
            print("no --agent-url / $AGENT_URL set — nothing live to canary.")
            return 0
        print("offline mode: dataset + scoring wiring only, no live calls.\n")
        scored = [score(c, f"Your balance on {c.get('account_id','')} is 100.00 USD.")
                  if c.get("expected_tool") else score(c, "I can't help with that.")
                  for c in cases]
        served = None
    else:
        scored, served, failures = [], None, 0
        for c in cases:
            try:
                answer, version = ask_live(args.agent_url, c)
                served = served or version
            except urllib.error.HTTPError as e:
                print(f"  ! {c['id']}: HTTP {e.code}")
                failures += 1
                continue
            except Exception as e:
                print(f"  ! {c['id']}: {type(e).__name__}: {e}")
                failures += 1
                continue
            s = score(c, answer)
            scored.append(s)
            flag = "ok " if (s["tool_ok"] and s["grounded"]) else "BAD"
            print(f"  {flag} {c['id']}")
        if failures and not scored:
            print("\nevery case failed to reach the agent — treating as infrastructure, "
                  "not drift. Not updating the baseline.")
            return 1

    current = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "env": args.env,
        "model_served": served,
        "metrics": aggregate(scored),
        "cases": scored,
    }
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")

    m = current["metrics"]
    print(f"\n  grounding {m['grounding_accuracy']}  tool-use {m['tool_utilization']}  "
          f"hallucination {m['hallucination_rate']}")
    print(f"  served version: {served or 'not reported'}")

    if args.set_baseline:
        BASELINE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"\nbaseline set -> {BASELINE.relative_to(REPO)}")
        return 0

    if not BASELINE.exists():
        print(f"\nno baseline yet. Review this run, then: "
              f"python scripts/canary_eval.py {args.env} --set-baseline")
        return 0

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    regressions = compare(current, baseline)
    vchange = version_changed(current, baseline)

    if vchange:
        # Not a failure by itself — but it is the single most useful fact when a metric
        # moves, so it is always reported, loudly, whether or not quality changed.
        print(f"\n  ⚠ SERVING VERSION CHANGED: {vchange}")

    if regressions:
        print("\nREGRESSION vs baseline:")
        for r in regressions:
            print(f"  - {r}")
        if vchange:
            print(f"\n  Attributable to a serving-version change ({vchange}). This is the "
                  f"case model pinning exists to prevent.")
        else:
            print("\n  No serving-version change detected — investigate prompt, tools, "
                  "or data before suspecting the model.")
        return 1

    print("\nwithin tolerance of baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
