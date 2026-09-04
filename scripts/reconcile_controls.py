#!/usr/bin/env python3
"""
Reconciliation control (ADR-0026, docs/26 §3).

The notification plane is lossy by design: log-based alerting caps at 20 notifications
per policy per day, Pub/Sub is at-least-once, Eventarc can drop on a sustained outage,
and a hibernating PDI is indistinguishable from one that is merely slow. Any of those
means a control fired in Google Cloud and nobody was told — which is the exact failure
the whole design exists to make visible.

So this compares the two planes and treats disagreement as its own control failure:

    evidence (BigQuery, written by a Cloud Logging sink)
        vs
    delivery (ServiceNow em_event, written by the dispatch workflow)

joined on `message_key`.

**It reconciles against `em_event`, not `incident`.** Delivery and promotion are different
questions. Every control event should reach `em_event` whatever its environment; whether it
becomes an incident is a policy decision owned by `em_alert_management_rule`, and a nonprod
event that correctly never became an incident is not a dropped notification. Reconciling
against incidents would report policy as failure.

Divergence in either direction matters:

  * in evidence but not in ServiceNow -> a DROPPED notification. The headline case.
  * in ServiceNow but not in evidence -> an UNEXPLAINED event. Either something is writing
    to em_event that is not this pipeline, or the evidence sink is losing rows. Both are
    worth knowing; the second is worse, because it means the audit trail is incomplete.

Usage:
    python scripts/reconcile_controls.py --project P --dataset finchat_platform_dev \\
        --instance https://dev305242.service-now.com --user gcp_integration --window-hours 24

The password comes from SN_PASSWORD or Secret Manager (--secret), never an argument.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict

# Cloud Logging names its own destination table after the log id; for Cloud Run stdout
# that is this. It does not exist until the first control event is written.
DEFAULT_TABLE = "run_googleapis_com_stdout"


# ---------------------------------------------------------------------------
# Pure logic. No BigQuery, no HTTP — this is the part that has to be right, and it
# is the part a test can actually pin.
# ---------------------------------------------------------------------------

@dataclass
class Reconciliation:
    matched: list = field(default_factory=list)
    dropped: list = field(default_factory=list)       # evidence says yes, ServiceNow says no
    unexplained: list = field(default_factory=list)   # ServiceNow says yes, evidence says no
    duplicated: list = field(default_factory=list)    # ServiceNow wrote a key more often than it happened

    @property
    def evidence_count(self) -> int:
        return len(self.matched) + len(self.dropped)

    @property
    def delivered_count(self) -> int:
        return len(self.matched) + len(self.unexplained)

    @property
    def duplicate_writes(self) -> int:
        return sum(d["excess"] for d in self.duplicated)

    @property
    def clean(self) -> bool:
        return not self.dropped and not self.unexplained and not self.duplicated


def normalise_keys(rows, key_field: str = "message_key") -> set:
    """Message keys from a result set, ignoring rows that carry none.

    A row without a message_key is not a near-miss to be matched loosely — it cannot
    correlate in Event Management either, so counting it here would report a delivery
    success that did not happen.
    """
    out = set()
    for r in rows or []:
        k = (r.get(key_field) or "").strip() if isinstance(r, dict) else ""
        if k:
            out.add(k)
    return out


def count_keys(rows, key_field: str = "message_key", count_field: str | None = None):
    """How many times each message key occurs.

    Set membership answers "did this arrive at all", which is why over-delivery was
    invisible: three writes of one key look exactly like one. The evidence side is already
    aggregated by the SQL and carries its own `occurrences`; the ServiceNow side is one row
    per event, so a row counts as one.
    """
    counts = Counter()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        k = (r.get(key_field) or "").strip()
        if not k:
            continue
        n = 1
        if count_field is not None:
            try:
                n = max(1, int(r.get(count_field) or 1))
            except (TypeError, ValueError):
                n = 1
        counts[k] += n
    return counts


def reconcile(evidence_rows, servicenow_rows) -> Reconciliation:
    ev_counts = count_keys(evidence_rows, count_field="occurrences")
    sn_counts = count_keys(servicenow_rows)
    ev, sn = set(ev_counts), set(sn_counts)

    # More ServiceNow rows than the control actually fired. One violation written three
    # times is the signature of a fan-out: several pipelines consuming the same event
    # (docs/26 F19). Correlation hides it downstream, because the duplicates share a
    # message_key and collapse into a single alert, so this is the only place it shows.
    duplicated = [
        {"key": k, "evidence": ev_counts[k], "delivered": sn_counts[k],
         "excess": sn_counts[k] - ev_counts[k]}
        for k in sorted(ev & sn) if sn_counts[k] > ev_counts[k]
    ]
    duplicated.sort(key=lambda d: (-d["excess"], d["key"]))

    return Reconciliation(
        matched=sorted(ev & sn),
        dropped=sorted(ev - sn),
        unexplained=sorted(sn - ev),
        duplicated=duplicated,
    )


def verdict(recon: Reconciliation) -> dict:
    """Turn a reconciliation into a control outcome.

    Severity mirrors the em_event scale so the resulting control-failure event routes
    through the same promotion rules as everything else: 2 = Major, 3 = Minor, 4 = Warning.

    A dropped notification outranks everything else. A dropped event means a real violation
    went unticketed; an unexplained event usually means a second writer, and a duplicated one
    means the same violation was written more than once. Both are untidy rather than
    dangerous, so they are Minor — unless there are no matches at all, which suggests the
    evidence sink itself is broken.

    Duplicates matter despite being Minor, because nothing downstream will ever report them:
    the extra rows share a message_key, Event Management correlates them into one alert, and
    every count a reviewer checks continues to look correct (docs/26 F19).
    """
    if recon.clean:
        return {"ok": True, "severity": "4", "summary":
                f"reconciled clean: {len(recon.matched)} control events delivered"}

    parts = []
    if recon.dropped:
        parts.append(f"{len(recon.dropped)} control event(s) never reached ServiceNow")
    if recon.unexplained:
        parts.append(f"{len(recon.unexplained)} ServiceNow event(s) have no evidence record")
    if recon.duplicated:
        parts.append(f"{len(recon.duplicated)} key(s) written more often than they occurred "
                     f"({recon.duplicate_writes} extra event(s))")

    severity = "2" if recon.dropped else "3"
    if recon.unexplained and not recon.matched and not recon.dropped:
        # Nothing matched at all: more likely the evidence side is missing than that every
        # single delivery was spurious.
        severity = "2"
    return {"ok": False, "severity": severity, "summary": "; ".join(parts)}


def control_event(recon: Reconciliation, v: dict, environment: str) -> dict:
    """The reconciliation result, as a control event of its own.

    Deliberately the same envelope every other control point emits, so the outcome of the
    reconciliation travels the pipeline it is checking. If that delivery also fails, the
    next run reports it — the control is self-checking without being self-referential.
    """
    return {
        "control_id": "reconciliation.controls_alerting",
        "source": "reconciliation",
        "environment": environment,
        "severity": "INFO" if v["ok"] else "WARNING",
        "message_key": f"reconciliation:controls_alerting:{environment}",
        "em_severity": v["severity"],
        "summary": v["summary"],
        "evidence_count": recon.evidence_count,
        "delivered_count": recon.delivered_count,
        "dropped_keys": recon.dropped[:20],
        "unexplained_keys": recon.unexplained[:20],
        "duplicate_writes": recon.duplicate_writes,
        "duplicated_keys": [f"{d['key']} x{d['delivered']} (expected {d['evidence']})"
                            for d in recon.duplicated[:20]],
    }


def evidence_sql(project: str, dataset: str, table: str, window_hours: int) -> str:
    """Flatten the envelope out of the Logging sink's table.

    The sink owns the table name and the outer schema; only `jsonPayload.control_event`
    is ours. Reconciliation events are excluded or each run would find its own previous
    output and reconcile the reconciler.
    """
    return f"""
    SELECT
      jsonPayload.control_event.message_key AS message_key,
      ANY_VALUE(jsonPayload.control_event.control_id)  AS control_id,
      ANY_VALUE(jsonPayload.control_event.source)      AS source,
      ANY_VALUE(jsonPayload.control_event.environment) AS environment,
      COUNT(*)  AS occurrences,
      MIN(timestamp) AS first_seen
    FROM `{project}.{dataset}.{table}`
    WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {window_hours} HOUR)
      AND jsonPayload.control_event.message_key IS NOT NULL
      AND jsonPayload.control_event.source != 'reconciliation'
    GROUP BY message_key
    """


def servicenow_query(window_hours: int) -> dict:
    """Encoded query for em_event over the same window.

    RELATIVEGE/hour is ServiceNow's relative-date operator; using it rather than a literal
    timestamp avoids the instance and the runner disagreeing about clocks or time zones,
    which would silently shift the comparison window and manufacture divergence.
    """
    return {
        "sysparm_query": f"source=GCP^sys_created_onRELATIVEGE@hour@ago@{window_hours}",
        "sysparm_fields": "message_key,sys_created_on,type,severity",
        "sysparm_limit": "10000",
    }


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def fetch_evidence(project, dataset, table, window_hours):
    from google.cloud import bigquery
    client = bigquery.Client(project=project)
    try:
        return [dict(r) for r in client.query(evidence_sql(project, dataset, table, window_hours))]
    except Exception as e:
        # A missing table is the expected state before the first control event, not an error.
        if "Not found" in str(e) or "404" in str(e):
            print(f"evidence table {dataset}.{table} does not exist yet — no events recorded",
                  file=sys.stderr)
            return []
        raise


def fetch_servicenow(instance, user, password, window_hours):
    import requests
    r = requests.get(f"{instance.rstrip('/')}/api/now/table/em_event",
                     params=servicenow_query(window_hours),
                     auth=(user, password), timeout=60)
    r.raise_for_status()
    return r.json().get("result", [])


def resolve_password(project: str, secret: str) -> str:
    pw = os.getenv("SN_PASSWORD", "")
    if pw:
        return pw
    if not secret:
        raise SystemExit("no password: set SN_PASSWORD or pass --secret")
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret}/versions/latest"
    return client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--table", default=DEFAULT_TABLE)
    p.add_argument("--instance", required=True)
    p.add_argument("--user", default="gcp_integration")
    p.add_argument("--secret", default="")
    p.add_argument("--window-hours", type=int, default=24)
    p.add_argument("--environment", default="dev")
    p.add_argument("--emit", action="store_true",
                   help="Print the outcome as a control event on stdout, so it flows through "
                        "the same pipeline as everything else.")
    p.add_argument("--fail-on-divergence", action="store_true",
                   help="Exit non-zero when the planes disagree (for CI gating).")
    a = p.parse_args(argv)

    evidence = fetch_evidence(a.project, a.dataset, a.table, a.window_hours)
    password = resolve_password(a.project, a.secret)
    delivered = fetch_servicenow(a.instance, a.user, password, a.window_hours)

    recon = reconcile(evidence, delivered)
    v = verdict(recon)

    if a.emit:
        ev = control_event(recon, v, a.environment)
        sys.stdout.write(json.dumps({
            "severity": ev["severity"],
            "message": f"control_event reconciliation/{ev['control_id']}",
            "control_event": ev,
        }, separators=(",", ":")) + "\n")
    else:
        print(json.dumps({"verdict": v, "reconciliation": asdict(recon)}, indent=2, default=str))

    print(f"[reconcile] {v['summary']}", file=sys.stderr)
    if a.fail_on_divergence and not v["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
