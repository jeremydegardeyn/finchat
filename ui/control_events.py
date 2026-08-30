"""
Canonical technical-control event envelope (docs/26, ADR-0026).

One contract, many emitters. Model Armor and DLP emit from here; Cloud Composer emits the
same shape from `orchestration/composer/dags/utils/alerting.py`; SCC would emit it from a
continuous export. Downstream, a single log-based alert policy routes all of them to
ServiceNow Event Management, which owns correlation and incident promotion.

Two design rules, both load-bearing:

1. **No payload content, by construction.** Model Armor's sanitize logs are the only place
   it stores the prompt and response, and a flagged prompt is exactly the text you must not
   copy into a ticket that a whole assignment group can read. This module therefore has no
   free-text field at all — `filters` is a list of detector names, nothing carries user
   input, and `test_control_events.py` asserts the key set never grows. Redaction you have
   to remember to apply is redaction you eventually forget.

2. **`environment` here is advisory, not authoritative.** It is set by the emitting process,
   so it is only as trustworthy as the process. The authoritative value is
   `resource.labels.env` on the log entry, which Cloud Run stamps from Terraform and the
   workload cannot forge; that is what the alert policy's labelExtractor should read. The
   field is carried anyway so the event is self-describing in BigQuery. See docs/26 F18 —
   FinChat's dev/test/prod share one project, so environment is a label, not a boundary.

Emission is a JSON line on stdout. Cloud Run hands that to Cloud Logging, which parses it
into `jsonPayload` — no client library, no network call, no credentials, and nothing that
can fail the request or get starved by post-response CPU throttling.

Gated by `CONTROL_EVENTS=1`; off, `emit()` is a no-op. Deploying this changes nothing until
the flag is set.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# --- taxonomy ---------------------------------------------------------------
# Closed vocabularies. A value outside these is a bug in the caller, not user input.

SOURCES = ("model_armor", "dlp", "composer", "scc")

SEVERITIES = ("INFO", "WARNING", "ERROR", "CRITICAL")

# The exact key set of an emitted envelope. The guard test pins this: adding a field is a
# deliberate contract change that has to be made here, reviewed, and mirrored in the
# Composer emitter and the BigQuery schema.
ENVELOPE_KEYS = frozenset({
    "control_id",
    "source",
    "environment",
    "severity",
    "message_key",
    "occurred_at",
    "principal_hash",
    "evidence_ref",
    "filters",
})

ENV = os.getenv("CONTROL_EVENT_ENV") or os.getenv("ENV") or "unknown"
SALT = os.getenv("CONTROL_EVENT_SALT", "")


def enabled() -> bool:
    return os.getenv("CONTROL_EVENTS", "").lower() in ("1", "true", "yes")


def principal_hash(principal: str | None) -> str:
    """Pseudonymise the actor.

    Salted so the digest is not a plain rainbow-table lookup of an email address. With
    CONTROL_EVENT_SALT unset the digest is still stable and still non-obvious, but it is
    only weakly pseudonymous — set the salt in any environment where the evidence table is
    readable more widely than the identities in it.
    """
    if not principal:
        return "anonymous"
    return hashlib.sha256(f"{SALT}:{principal}".encode("utf-8")).hexdigest()[:16]


# Detector classes, ordered by seriousness. Correlation keys on the CLASS, never on the
# exact filter set.
#
# Keying on the set was the first design and it fragmented the queue: the same jailbreak
# tripped `["pi_and_jailbreak"]` on one attempt and `["pi_and_jailbreak","rai"]` on the
# next, and Event Management — correctly — treated those as two incidents. An attacker
# probing for five minutes would open a fresh ticket every time the detector mix shifted,
# which is the exact flooding correlation exists to prevent.
#
# The class is stable across attempts of the same attack while still separating things a
# responder handles differently. The full filter list still travels in the event, so
# nothing is lost — only the grouping is coarser.
FILTER_CLASSES = (
    ("security", ("pi_and_jailbreak", "malicious_uris")),  # someone attacking the system
    ("privacy", ("sdp",)),                                 # sensitive data crossing a boundary
    ("content", ("rai",)),                                 # a user being unpleasant
)


def filter_class(filters: list[str] | None) -> str:
    """The single class a violation belongs to. Highest seriousness wins when several fire."""
    fired = set(filters or [])
    for name, members in FILTER_CLASSES:
        if fired & set(members):
            return name
    return "unclassified"


def message_key(source: str, control_id: str, *parts: str) -> str:
    """Correlation key. Event Management collapses events sharing one into a single alert.

    This is what turns N Airflow retries into one incident, and a burst of prompt-injection
    attempts by one principal into one incident. Deliberately coarse: it omits the
    timestamp, so repeats collapse rather than accumulating. Getting this wrong in the
    lenient direction floods the on-call queue; getting it wrong in the strict direction
    hides a second, genuinely different failure behind the first.
    """
    return ":".join([source, control_id, *(p for p in parts if p)])


def build(
    *,
    control_id: str,
    source: str,
    severity: str = "WARNING",
    principal: str | None = None,
    evidence_ref: str | None = None,
    filters: list[str] | None = None,
    key_parts: tuple[str, ...] = (),
    environment: str | None = None,
    occurred_at: str | None = None,
) -> dict:
    """Construct an envelope. Raises on an out-of-vocabulary source or severity.

    Note what this signature does *not* accept: any prompt, response, exception message, SQL,
    or row value. That is the point — see rule 1 in the module docstring.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}; expected one of {SEVERITIES}")

    return {
        "control_id": control_id,
        "source": source,
        "environment": environment or ENV,
        "severity": severity,
        "message_key": message_key(source, control_id, *key_parts),
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
        "principal_hash": principal_hash(principal),
        "evidence_ref": evidence_ref or "",
        "filters": sorted(filters or []),
    }


def emit(event: dict) -> None:
    """Write one structured line to stdout for Cloud Logging to pick up.

    Never raises. A control event that takes down the request it is describing would be a
    worse failure than the one it reports — and the evidence plane is not the only record:
    Model Armor's own sanitize log is written by the service regardless of what this does.
    """
    if not enabled():
        return
    try:
        sys.stdout.write(json.dumps({
            "severity": event.get("severity", "WARNING"),
            "message": f"control_event {event.get('source')}/{event.get('control_id')}",
            "control_event": event,
        }, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:  # pragma: no cover - emission must never break the caller
        pass


def emit_armor_block(
    *,
    direction: str,
    filters: list[str] | None,
    principal: str | None,
    trace: str | None,
    environment: str | None = None,
) -> dict:
    """Convenience emitter for a Model Armor block. `direction` is 'prompt' or 'response'.

    `trace` should be the X-Cloud-Trace-Context id: it is what lets a responder pivot from
    this redacted event to the raw sanitize log entry — which holds the actual flagged text
    — under GCP IAM, without that text ever entering ServiceNow.
    """
    matched = sorted(filters or [])
    event = build(
        control_id=f"model_armor.{direction}",
        source="model_armor",
        severity="WARNING",
        principal=principal,
        evidence_ref=trace,
        filters=matched,
        # One incident per principal per detector CLASS, not per attempt and not per
        # filter set — see FILTER_CLASSES for why the set was the wrong key.
        key_parts=(principal_hash(principal), filter_class(matched)),
        environment=environment,
    )
    emit(event)
    return event


# --- routing matrix ---------------------------------------------------------
# Intent, expressed as data. In the target design ServiceNow Event Management owns
# promotion (`em_alert_management_rule`) so it is auditable and changeable without a
# deploy; this table is the specification those rules implement, the seed for configuring
# them, and the decision function for the fallback path if Event Management turns out not
# to be licensed on the PDI (docs/26 F9, A5).
#
# Matched top to bottom, first hit wins. `env: "*"` matches anything.

ROUTING = (
    {"source": "model_armor", "env": "prod", "incident": True,  "priority": "P2", "teams": "via_sn"},
    {"source": "model_armor", "env": "*",    "incident": False, "priority": None, "teams": "direct"},
    {"source": "dlp",         "env": "prod", "incident": True,  "priority": "P2", "teams": "via_sn"},
    {"source": "dlp",         "env": "*",    "incident": False, "priority": None, "teams": "direct"},
    {"source": "composer",    "env": "prod", "incident": True,  "priority": "P3", "teams": "via_sn"},
    {"source": "composer",    "env": "*",    "incident": False, "priority": None, "teams": "direct"},
    {"source": "scc",         "env": "*",    "incident": False, "priority": None, "teams": None},
)

DEFAULT_ROUTE = {"incident": False, "priority": None, "teams": None}


def route(event: dict) -> dict:
    """Decide what an event should cause. Pure function over the table above."""
    src, env = event.get("source"), event.get("environment")
    for rule in ROUTING:
        if rule["source"] == src and rule["env"] in (env, "*"):
            return {k: rule[k] for k in ("incident", "priority", "teams")}
    return dict(DEFAULT_ROUTE)
