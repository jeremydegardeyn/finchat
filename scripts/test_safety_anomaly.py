"""Tier 3 population anomaly detection (ADR-0034) — the pure comparison, offline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import safety_anomaly as sa  # noqa: E402


def bucket(**kw):
    b = {"turns": 40, "sessions": 20, "security_rate": 0.05, "wellbeing_rate": 0.05,
         "refusal_rate": 0.30, "unscreened_rate": 0.0, "product_actions": 0,
         "review_events": 1, "probing_sessions": 1, "model_served": "gemini-x@001"}
    b.update(kw)
    return b


def baseline(n=40, **kw):
    # A little variance, so std is not zero and z is well defined.
    rows = []
    for i in range(n):
        rows.append(bucket(security_rate=0.05 + (0.01 if i % 2 else -0.01),
                           refusal_rate=0.30 + (0.02 if i % 2 else -0.02),
                           wellbeing_rate=0.05 + (0.01 if i % 3 else 0), **kw))
    return rows


def names(found):
    return {a.metric for a in found}


def test_quiet_bucket_is_quiet():
    assert sa.detect(bucket(), baseline()) == []


def test_probing_campaign_fires_on_rate_and_on_sessions():
    cur = bucket(security_rate=0.40, probing_sessions=9)
    found = sa.detect(cur, baseline())
    assert {"security_rate", "probing_sessions"} <= names(found)
    sec = next(a for a in found if a.metric == "security_rate")
    assert sec.severity == "ERROR" and sec.score > 3


def test_refusal_collapse_fires_downward_only():
    assert "refusal_rate" in names(sa.detect(bucket(refusal_rate=0.02), baseline()))
    assert "refusal_rate" not in names(sa.detect(bucket(refusal_rate=0.60), baseline()))


def test_refusal_collapse_correlates_per_model_version():
    a = next(x for x in sa.detect(bucket(refusal_rate=0.02, model_served="m@002"), baseline())
             if x.metric == "refusal_rate")
    ev = sa.control_event(a, "prod")
    assert ev["message_key"].endswith(":m@002") and "below_baseline" in ev["filters"]


def test_thin_baseline_produces_nothing():
    """Day one of a deployment: no baseline, no anomalies, by design."""
    assert sa.detect(bucket(security_rate=0.9), baseline(n=5)) == []


def test_small_bucket_rates_are_ignored_but_counts_are_not():
    cur = bucket(turns=3, security_rate=1.0, probing_sessions=8)
    found = sa.detect(cur, baseline())
    assert "security_rate" not in names(found)
    assert "probing_sessions" in names(found)


def test_zero_variance_baseline_needs_a_real_move():
    flat = [bucket() for _ in range(40)]
    assert sa.detect(bucket(security_rate=0.07), flat) == []          # 2 points: noise
    assert "security_rate" in names(sa.detect(bucket(security_rate=0.30), flat))


def test_count_floor_stops_one_event_from_firing_on_an_empty_baseline():
    flat = [bucket(product_actions=0) for _ in range(40)]
    assert "product_actions" not in names(sa.detect(bucket(product_actions=2), flat))
    assert "product_actions" in names(sa.detect(bucket(product_actions=4), flat))


def test_envelope_matches_the_pinned_key_set():
    a = sa.Anomaly("security_rate", 0.4, 0.05, 0.01, 35.0, "ERROR", "why", "m")
    ev = sa.control_event(a, "dev")
    assert set(ev) == {"control_id", "source", "environment", "severity", "message_key",
                       "occurred_at", "principal_hash", "evidence_ref", "filters"}
    assert ev["source"] == "conversation_safety" and ev["principal_hash"] == "population"
    assert ev["message_key"] == "conversation_safety:population.security_rate"
