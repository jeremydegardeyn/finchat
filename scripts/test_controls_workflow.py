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
    assert 'environment == "prod"' in SOURCE


def test_does_not_assign_to_reserved_workflow_variables():
    """`env` is reserved in Workflows and the deploy fails on it — which is a slow way to
    find out, so pin the whole reserved set here instead."""
    reserved = ("env", "sys", "text", "json", "map", "list", "http", "base64", "time", "math")
    for step in yaml.safe_load(SOURCE)["main"]["steps"]:
        body = list(step.values())[0]
        for assignment in (body.get("assign", []) if isinstance(body, dict) else []):
            for name in assignment:
                assert name not in reserved, f"assigns to reserved variable {name!r}"


def test_maps_destined_for_json_are_built_as_yaml_blocks():
    """Workflows expressions cannot contain map literals; `{...}` inside ${} fails to
    parse at deploy time."""
    assert 'json.encode_to_string({' not in SOURCE.replace(" ", "")


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


def test_event_time_is_converted_to_servicenow_date_format():
    """ServiceNow wants "YYYY-MM-DD HH:MM:SS". Handed an ISO-8601 string it takes the date
    and silently zeroes the time — the first end-to-end run put every event at 00:00:00,
    which destroys chronology in the system a responder actually reads, and fails quietly."""
    assert "format_event_time" in SOURCE
    assert "time_of_event: ${sn_time}" in SOURCE
    assert 'text.substring(iso_parts[1], 0, 8)' in SOURCE


def test_event_time_conversion_is_guarded_against_a_non_iso_value():
    """An unparseable occurred_at must yield an empty string, not a malformed date that
    ServiceNow rejects or mangles."""
    spec = yaml.safe_load(SOURCE)
    names = [list(s)[0] for s in spec["main"]["steps"]]
    assert names.index("format_event_time") < names.index("convert_event_time")
    assert names.index("convert_event_time") < names.index("build_row")
    assert '- sn_time: ""' in SOURCE


# --- Teams (optional second destination) -------------------------------------

def test_teams_hop_is_skipped_when_unconfigured():
    """Empty secret id must skip, not fail — most environments will not set it."""
    assert 'sys.get_env("TEAMS_SECRET_ID") == ""' in SOURCE
    assert "teams_gate" in SOURCE


def test_teams_runs_after_servicenow_and_cannot_fail_the_execution():
    """ServiceNow is the record; Teams is awareness. A broken webhook must not cost the
    auditable write, which is the entire reason the two planes are separate."""
    names = [list(s)[0] for s in yaml.safe_load(SOURCE)["main"]["steps"]]
    assert names.index("post_event") < names.index("notify_teams")
    teams = [s for s in yaml.safe_load(SOURCE)["main"]["steps"] if "notify_teams" in s][0]
    assert "except" in teams["notify_teams"]


def test_teams_card_carries_no_content():
    """Same redaction rule as the em_event row — detectors, never what they fired on."""
    card = SOURCE[SOURCE.index("- card:"):SOURCE.index("- post_card:")]
    for leaky in ("occurred", "jsonPayload", "textPayload", "prompt", "response"):
        assert leaky not in card, f"Teams card references {leaky!r}"


def test_teams_card_links_back_to_the_system_of_record():
    """Posting from the workflow rather than from ServiceNow costs us the incident number;
    the event link is what lets a responder cross over anyway."""
    assert "nav_to.do?uri=em_event.do?sys_id=" in SOURCE


def test_teams_webhook_comes_from_secret_manager():
    """A Teams webhook URL is a credential: anyone holding it can post to the channel."""
    assert 'secret_id: ${sys.get_env("TEAMS_SECRET_ID")}' in SOURCE
    assert "https://" not in SOURCE.split("notify_teams")[1].split("- done:")[0]
