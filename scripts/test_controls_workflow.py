#!/usr/bin/env python3
"""
Drift guard on the controls-alerting dispatch workflow (ADR-0026).

The workflow is the one place where a control event crosses out of Google Cloud and into
a third-party system. There is no unit test that can run it — it needs Eventarc, Secret
Manager and a live ServiceNow — so these assertions stand in for one, and they guard the
properties that would be expensive to discover in production:

  * it never references a field that could carry user content
  * it sends `message_key`, without which Event Management cannot correlate at all
  * it derives environment from resource identity, not from the payload

Run: pytest scripts/test_controls_workflow.py -q   (needs pyyaml)
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[1] / "infra" / "modules" / "controls_alerting" / "workflow.yaml"
SOURCE = WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_valid_yaml():
    assert yaml.safe_load(SOURCE) is not None


def test_workflow_has_a_main_with_the_eventarc_param():
    spec = yaml.safe_load(SOURCE)
    assert "main" in spec
    assert spec["main"]["params"] == ["event"]


# --- the redaction guard ----------------------------------------------------

CONTENT_BEARING = (
    "textPayload",          # the unstructured log body
    "protoPayload",         # audit-log bodies carry request contents
    "user_prompt_data",     # Model Armor request shape
    "model_response_data",
    "sanitizationResult",   # the raw Model Armor result, which quotes matched content
    "deidentifyResult",
)


def test_workflow_never_reads_a_content_bearing_field():
    """Model Armor's sanitize log holds the flagged prompt. Nothing that crosses into
    ServiceNow may be sourced from it — see ADR-0026 point 4."""
    for field in CONTENT_BEARING:
        assert field not in SOURCE, f"workflow references content-bearing field {field!r}"


def test_workflow_reads_only_the_control_event_envelope():
    assert 'map.get(entry, ["jsonPayload", "control_event"])' in SOURCE


# --- correlation ------------------------------------------------------------

def test_message_key_is_sent():
    """Without it Event Management treats every event as unique and correlation is dead —
    which would put us right back to one incident per Airflow retry."""
    assert "message_key:" in SOURCE


def test_posts_to_em_event_not_incident():
    """em_event keeps promotion in ServiceNow's hands. Writing straight to `incident`
    would move correlation into this workflow, which ADR-0026 rejects."""
    assert "/api/now/table/em_event" in SOURCE
    assert "/api/now/table/incident" not in SOURCE


# --- environment trust ------------------------------------------------------

def test_environment_prefers_resource_identity_over_the_payload():
    """The payload's `environment` is emitter-set and forgeable; the Cloud Run service
    name is stamped by the platform (docs/26 F18)."""
    assert '"resource", "labels", "service_name"' in SOURCE
    assert "env_from_resource_identity" in SOURCE


def test_severity_distinguishes_prod_from_nonprod():
    spec = yaml.safe_load(SOURCE)
    steps = {list(s)[0] for s in spec["main"]["steps"]}
    assert "derive_severity" in steps
    assert 'env == "prod"' in SOURCE


# --- resilience -------------------------------------------------------------

def test_post_is_retried():
    """A hibernating PDI is indistinguishable from an outage; both need backoff."""
    assert "max_retries" in SOURCE
    assert "http.default_retry_predicate" in SOURCE


def test_credentials_come_from_secret_manager_not_the_workflow_body():
    assert "secretmanager.v1.projects.secrets.versions.accessString" in SOURCE
    for leak in ("password:", "passwd", "Basic Z"):
        assert leak not in SOURCE


def test_non_control_events_are_dropped_before_the_post():
    """A sink filter is coarse; the workflow must not turn a stray entry into a row."""
    spec = yaml.safe_load(SOURCE)
    step_names = [list(s)[0] for s in spec["main"]["steps"]]
    assert step_names.index("guard_shape") < step_names.index("post_event")
