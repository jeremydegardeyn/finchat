#!/usr/bin/env python3
"""
Population-level safety anomaly detection — tier 3 of ADR-0034.

Tiers 1 and 2 (ui/safety_signals.py) answer "is THIS conversation going wrong" and they
do it in the request, because a single customer in trouble or a single attacker probing
is a known pattern, not a statistical deviation. This script answers the other question:
"is the SYSTEM going wrong" — and that one IS anomaly detection, because the things it
looks for have no per-conversation signature:

  * a probing campaign: security signals across many sessions at once
  * a safety regression: refusals collapsing after a silent model version change
  * a screening outage: the share of unscreened turns climbing
  * a wellbeing cluster: a spike in distressed conversations (an outage of the bank's
    own systems, a news event, a scam wave)

Method. `safety_buckets` (scripts/eval_schema.sql) is a 15-minute series of rates over
`turn_signals`. The latest complete bucket is compared to the trailing 28-day baseline
of the same series: a z-score for rates, and — because at low volume a standard deviation
of zero is the common case and z is undefined — a Poisson-style bound for counts,
`x > mean + 3*sqrt(mean)` with a floor. Both are deliberately simple: an anomaly that a
reviewer cannot recompute by hand is an anomaly they will not act on.

Latency. This runs every 15 minutes (.github/workflows/safety-anomaly.yml); the enterprise
mapping is a Cloud Run Job on Cloud Scheduler, or a Monitoring alert on a log-based
metric for the sub-minute, lossy, notification-only version. Tier 3 latency is its
window by construction — it cannot be "real time" and still be a baseline comparison.

Emission. Anomalies leave as ordinary control events with `source=conversation_safety`,
written through the Cloud Logging API so the SAME sink, workflow and ServiceNow rule that
carry a Model Armor block carry these. A GitHub-hosted runner has no Cloud Run stdout,
which is why this script cannot simply print the line the way the BFF does.

Usage:  python scripts/safety_anomaly.py [dev|prod] [--emit] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = "strongsville-city-schools"

# What we watch, and how. `kind` decides the test: rates get a z-score, counts get the
# Poisson bound. `min_turns` is the bucket size below which a rate is noise, not signal.
METRICS = (
    {"name": "security_rate",   "kind": "rate",  "z": 3.0, "min_turns": 10,
     "severity": "ERROR",   "why": "probing across sessions above baseline"},
    {"name": "probing_sessions", "kind": "count", "min_turns": 1,
     "severity": "ERROR",   "why": "distinct sessions probing above baseline (campaign)"},
    {"name": "wellbeing_rate",  "kind": "rate",  "z": 3.0, "min_turns": 10,
     "severity": "WARNING", "why": "distressed conversations above baseline"},
    {"name": "refusal_rate",    "kind": "rate",  "z": -3.0, "min_turns": 10,
     "severity": "ERROR",   "why": "refusals BELOW baseline — safety decay or model change"},
    {"name": "unscreened_rate", "kind": "rate",  "z": 3.0, "min_turns": 5,
     "severity": "ERROR",   "why": "turns served unscreened above baseline (classifier outage)"},
    {"name": "product_actions", "kind": "count", "min_turns": 1,
     "severity": "WARNING", "why": "hand-offs / quarantines above baseline"},
)


@dataclass
class Anomaly:
    metric: str
    value: float
    baseline_mean: float
    baseline_std: float
    score: float          # z for rates; (x - mean)/sqrt(mean) for counts
    severity: str
    why: str
    model_served: str | None = None
    extra: dict = field(default_factory=dict)


def _mean_std(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    return m, math.sqrt(var)


def detect(current: dict, baseline: list[dict], metrics=METRICS,
           min_baseline_buckets: int = 20) -> list[Anomaly]:
    """Compare one bucket to its baseline. Pure — this is what the tests exercise.

    `current` and each baseline row are `safety_buckets` rows as dicts. A metric with too
    thin a baseline is skipped rather than compared against noise: the first day of a
    deployment produces no anomalies, by design, and that is stated in the output.
    """
    out: list[Anomaly] = []
    if not current:
        return out
    turns = float(current.get("turns") or 0)
    for m in metrics:
        name = m["name"]
        if turns < m["min_turns"]:
            continue
        hist = [float(b[name]) for b in baseline if b.get(name) is not None]
        if len(hist) < min_baseline_buckets:
            continue
        x = float(current.get(name) or 0.0)
        mean, std = _mean_std(hist)
        if m["kind"] == "rate":
            if std == 0.0:
                # Every baseline bucket identical. Any different value is a deviation, but
                # only in the watched direction, and only if it is not trivially small.
                score = 0.0 if x == mean else math.copysign(float("inf"), x - mean)
            else:
                score = (x - mean) / std
            want = m["z"]
            hit = score >= want if want > 0 else score <= want
            if hit and abs(x - mean) >= 0.05:   # a 5-point move in a rate, at least
                out.append(Anomaly(name, x, mean, std, score, m["severity"], m["why"],
                                   current.get("model_served")))
        else:  # count: Poisson bound with a floor so a baseline of ~0 does not fire on 1
            bound = max(3.0, mean + 3.0 * math.sqrt(max(mean, 0.0)))
            if x > bound:
                score = (x - mean) / math.sqrt(mean) if mean > 0 else float("inf")
                out.append(Anomaly(name, x, mean, std, score, m["severity"], m["why"],
                                   current.get("model_served"), {"bound": bound}))
    return out


def control_event(a: Anomaly, environment: str) -> dict:
    """The anomaly as the canonical envelope (ui/control_events.py). Built by hand here
    for the same reason reconcile_controls.py builds its own: this runs on a GitHub
    runner with no ui/ on the path, and the key set is pinned by test in both repos.
    Correlation is per metric (and per model version for the refusal collapse, because a
    second model change is a second incident), never per run — a persisting anomaly stays
    one alert."""
    key_parts = ["conversation_safety", f"population.{a.metric}"]
    if a.metric == "refusal_rate" and a.model_served:
        key_parts.append(a.model_served)
    return {
        "control_id": f"population.{a.metric}",
        "source": "conversation_safety",
        "environment": environment,
        "severity": a.severity,
        "message_key": ":".join(key_parts),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "principal_hash": "population",
        "evidence_ref": f"safety_buckets:{a.metric}",
        # Names only, as everywhere: the metric and the direction. The numbers are in
        # BigQuery, one query away, and do not belong in a ticket title.
        "filters": sorted({a.metric, "above_baseline" if a.score > 0 else "below_baseline"}),
    }


def fetch(project: str, dataset: str) -> tuple[dict | None, list[dict]]:
    from google.cloud import bigquery
    bq = bigquery.Client(project=project)
    rows = [dict(r) for r in bq.query(f"""
        SELECT * FROM `{project}.{dataset}.safety_buckets`
        WHERE bucket < TIMESTAMP_BUCKET(CURRENT_TIMESTAMP(), INTERVAL 15 MINUTE)
        ORDER BY bucket DESC""").result()]
    if not rows:
        return None, []
    # The latest COMPLETE bucket is current; everything before it is baseline. The
    # in-progress bucket is excluded above — a half-filled bucket is not a rate.
    return rows[0], rows[1:]


def emit(events: list[dict], project: str) -> None:
    """Write through the Logging API so the controls sink picks the entry up exactly as it
    would a BFF stdout line: `jsonPayload.control_event.*`, no service_name, environment
    from the payload (docs/26 sink filter, second branch)."""
    from google.cloud import logging as gcl
    logger = gcl.Client(project=project).logger("finchat-safety-anomaly")
    for ev in events:
        logger.log_struct({"message": f"control_event {ev['source']}/{ev['control_id']}",
                           "control_event": ev}, severity=ev["severity"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("env", nargs="?", default="dev")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--emit", action="store_true", help="write anomalies to Cloud Logging")
    ap.add_argument("--dry-run", action="store_true", help="print, do not emit")
    a = ap.parse_args(argv)
    dataset = f"finchat_eval_{a.env}"

    current, baseline = fetch(a.project, dataset)
    if not current:
        print(f"[safety_anomaly] {a.env}: no complete bucket yet"); return 0
    found = detect(current, baseline)
    print(f"[safety_anomaly] {a.env}: bucket={current['bucket']} turns={current['turns']} "
          f"baseline_buckets={len(baseline)} anomalies={len(found)}")
    for x in found:
        print(f"  ! {x.metric}={x.value:.3f} baseline={x.baseline_mean:.3f}±{x.baseline_std:.3f} "
              f"score={x.score:.2f} [{x.severity}] {x.why}")
    if found and a.emit and not a.dry_run:
        events = [control_event(x, a.env) for x in found]
        emit(events, a.project)
        print(f"  emitted {len(events)} control event(s)")
    elif found:
        print(json.dumps([control_event(x, a.env) for x in found], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
