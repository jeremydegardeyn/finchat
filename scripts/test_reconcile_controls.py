"""
Offline tests for the reconciliation control (ADR-0026).

No BigQuery, no ServiceNow, no network. Reconciliation is the control that says whether
every other control was actually reported, so its logic has to be right when nothing else
is available to check it against.
"""
import reconcile_controls as rc


def ev(*keys):
    return [{"message_key": k} for k in keys]


# --- the headline case ------------------------------------------------------

def test_event_in_evidence_but_not_servicenow_is_a_dropped_notification():
    r = rc.reconcile(ev("a", "b", "c"), ev("a", "b"))
    assert r.dropped == ["c"]
    assert r.matched == ["a", "b"]
    assert not r.unexplained


def test_a_dropped_notification_is_major():
    """A real violation went unticketed. That outranks every other divergence."""
    r = rc.reconcile(ev("a"), [])
    v = rc.verdict(r)
    assert v["ok"] is False
    assert v["severity"] == "2"
    assert "never reached ServiceNow" in v["summary"]


def test_clean_reconciliation_is_ok_and_low_severity():
    r = rc.reconcile(ev("a", "b"), ev("b", "a"))
    v = rc.verdict(r)
    assert v["ok"] is True
    assert v["severity"] == "4"
    assert r.clean


# --- the other direction ----------------------------------------------------

def test_servicenow_event_with_no_evidence_is_flagged_but_ranked_lower():
    """Usually a second writer — untidy rather than dangerous, so Minor not Major."""
    r = rc.reconcile(ev("a"), ev("a", "rogue"))
    v = rc.verdict(r)
    assert r.unexplained == ["rogue"]
    assert v["ok"] is False
    assert v["severity"] == "3"


def test_nothing_matching_at_all_is_major_not_minor():
    """If not one delivery has a matching evidence row, the likelier explanation is that
    the evidence sink is broken — which means the audit trail is incomplete."""
    r = rc.reconcile([], ev("x", "y"))
    assert rc.verdict(r)["severity"] == "2"


def test_both_directions_are_reported_together():
    r = rc.reconcile(ev("a", "gone"), ev("a", "rogue"))
    v = rc.verdict(r)
    assert r.dropped == ["gone"] and r.unexplained == ["rogue"]
    assert "never reached ServiceNow" in v["summary"]
    assert "no evidence record" in v["summary"]
    assert v["severity"] == "2"  # a drop dominates


# --- key handling -----------------------------------------------------------

def test_rows_without_a_message_key_are_ignored_not_matched_loosely():
    """An event with no message_key cannot correlate in Event Management either, so
    counting it would report a delivery that did not happen."""
    assert rc.normalise_keys([{"message_key": ""}, {"message_key": None}, {}]) == set()


def test_whitespace_around_keys_does_not_split_a_pair():
    r = rc.reconcile([{"message_key": " a "}], [{"message_key": "a"}])
    assert r.matched == ["a"] and r.clean


def test_duplicate_keys_collapse_rather_than_inflating_counts():
    """Three Airflow retries are one message_key; reconciliation must agree with Event
    Management's own collapsing or it will report drift that is not there."""
    r = rc.reconcile(ev("a", "a", "a"), ev("a"))
    assert r.matched == ["a"]
    assert r.evidence_count == 1
    assert r.clean


def test_empty_on_both_sides_is_clean():
    """Quiet period, not a failure."""
    assert rc.verdict(rc.reconcile([], []))["ok"] is True


# --- the emitted control event ----------------------------------------------

def test_outcome_is_emitted_as_the_same_envelope_everything_else_uses():
    r = rc.reconcile(ev("a", "b"), ev("a"))
    e = rc.control_event(r, rc.verdict(r), "prod")
    assert e["source"] == "reconciliation"
    assert e["environment"] == "prod"
    assert e["message_key"].startswith("reconciliation:")
    assert e["severity"] == "WARNING"


def test_clean_run_emits_info_not_warning():
    r = rc.reconcile(ev("a"), ev("a"))
    assert rc.control_event(r, rc.verdict(r), "dev")["severity"] == "INFO"


def test_emitted_key_lists_are_capped():
    """A total outage would otherwise put thousands of keys into one log line."""
    many = ev(*[f"k{i}" for i in range(100)])
    r = rc.reconcile(many, [])
    assert len(rc.control_event(r, rc.verdict(r), "dev")["dropped_keys"]) == 20


# --- query construction -----------------------------------------------------

def test_evidence_sql_excludes_reconciliation_events():
    """Otherwise each run finds its own previous output and reconciles the reconciler."""
    assert "source != 'reconciliation'" in rc.evidence_sql("p", "d", "t", 24)


def test_evidence_sql_groups_by_message_key():
    sql = rc.evidence_sql("p", "d", "t", 24)
    assert "GROUP BY message_key" in sql
    assert "INTERVAL 24 HOUR" in sql


def test_servicenow_query_uses_a_relative_window():
    """A literal timestamp would make the instance and the runner disagree about clocks
    and silently shift the comparison window."""
    q = rc.servicenow_query(6)
    assert "RELATIVEGE@hour@ago@6" in q["sysparm_query"]
    assert "source=GCP" in q["sysparm_query"]


def test_servicenow_query_asks_for_message_key():
    assert "message_key" in rc.servicenow_query(24)["sysparm_fields"]
