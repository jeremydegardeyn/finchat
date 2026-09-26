"""Tests for the managed-eval pipeline (vertex_eval.py).

What is worth testing here is everything that happens BEFORE the service is called, since
that is where this pipeline's history of being wrong lies: it previously shipped a
dataframe whose response column was a placeholder string, which would have produced
plausible metrics about nothing.

So: the frames carry real values or the run stops; the tool-call payload is shaped for the
computation metrics; the refusal rubric is derived from the policy SSOT rather than a copy
that can fall behind it; and an empty expected trajectory is preserved as an assertion
instead of being dropped as a blank.

The one thing these tests cannot cover is whether the service agrees with
`tool_calls_json()` about the payload shape — that is an API contract, and
`vertex_eval.py --probe` is how it gets checked, for no tokens.
"""
from __future__ import annotations

import json

import pytest

import vertex_eval as V


# --------------------------------------------------------------------------- #
# Expected trajectories
# --------------------------------------------------------------------------- #
def test_expected_calls_uses_the_labelled_tool_and_account():
    case = {"expected_tool": "get_account_balance", "account_id": "acct-001"}
    assert V.expected_calls(case) == [
        {"name": "get_account_balance", "arguments": {"account_id": "acct-001"}}]


def test_a_refusal_case_expects_no_tool_call():
    # An empty list, not None: "answered without touching customer data" is the
    # assertion, and tool_name_match scores it.
    assert V.expected_calls({"expected_tool": None, "account_id": "acct-001"}) == []


def test_every_dataset_case_produces_an_expectation():
    cases = V.load_cases()
    assert cases, "golden dataset is empty"
    for c in cases:
        calls = V.expected_calls(c)
        assert isinstance(calls, list)
        if c.get("expected_tool"):
            assert calls and calls[0]["name"] == c["expected_tool"]
        else:
            assert calls == []


# --------------------------------------------------------------------------- #
# The tool-metric payload
# --------------------------------------------------------------------------- #
def test_tool_calls_json_has_the_keys_the_metrics_read():
    payload = json.loads(V.tool_calls_json(
        [{"name": "get_account_summary", "arguments": {"account_id": "acct-002"}}]))
    assert set(payload) == {"content", "tool_calls"}
    assert payload["tool_calls"][0]["name"] == "get_account_summary"
    assert payload["tool_calls"][0]["arguments"] == {"account_id": "acct-002"}


def test_an_empty_trajectory_still_serializes_to_a_valid_payload():
    payload = json.loads(V.tool_calls_json([]))
    assert payload["tool_calls"] == []


# --------------------------------------------------------------------------- #
# Grounding context
# --------------------------------------------------------------------------- #
def test_context_is_built_from_tool_output():
    ctx = V.context_text([{"name": "get_account_balance",
                           "response": {"balance": 1234.56, "currency": "USD"}}])
    assert "get_account_balance" in ctx and "1234.56" in ctx


def test_a_turn_with_no_tool_call_has_no_context():
    # Deliberately empty. A refusal has no grounding evidence, and handing the judge
    # something anyway would invite it to score grounding it cannot assess.
    assert V.context_text([]) == ""


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #
def _rows():
    return [{
        "id": "bal-1", "prompt": "What is the balance on acct-001?",
        "response": "Your balance on acct-001 is 1234.56 USD.",
        "reference": "Reports the current balance with currency from the balance tool.",
        "context": "get_account_balance -> {\"balance\": 1234.56}",
        "calls": [{"name": "get_account_balance",
                   "arguments": {"account_id": "acct-001"}}],
        "expected_calls": [{"name": "get_account_balance",
                            "arguments": {"account_id": "acct-001"}}],
        "intermediate_events": [{"author": "agent", "content": {"parts": []}}],
    }]


def test_response_frame_columns():
    pytest.importorskip("pandas")
    df = V.response_frame(_rows())
    assert list(df.columns) == ["prompt", "response", "reference", "context"]
    assert "<deployed-agent-response>" not in df["response"].iloc[0]


def test_trajectory_frame_carries_json_on_both_sides():
    pytest.importorskip("pandas")
    df = V.trajectory_frame(_rows())
    assert json.loads(df["response"].iloc[0])["tool_calls"]
    assert json.loads(df["reference"].iloc[0])["tool_calls"]


def test_grounding_frame_puts_the_evidence_in_the_prompt():
    """Verified against the service 2026-09-26: hallucination_v1 reads its evidence from
    the prompt and nowhere else. Supplied via a `context` column, `instruction` or
    `reference` it scored a fabricated balance 1.0 (self-grounding, with the response
    returned as its own supporting excerpt); in the prompt it scored 0.0."""
    pytest.importorskip("pandas")
    df = V.grounding_frame(_rows())
    assert df is not None
    prompt = df["prompt"].iloc[0]
    assert "1234.56" in prompt, "tool output must be inside the prompt"
    assert "What is the balance on acct-001?" in prompt
    assert list(df.columns) == ["prompt", "response"]


def test_grounding_frame_skips_turns_with_no_evidence():
    # A declined turn has no tool output; an evidence-free prompt is what makes the metric
    # self-ground and report a meaningless pass.
    pytest.importorskip("pandas")
    rows = _rows()
    rows[0]["context"] = ""
    assert V.grounding_frame(rows) is None


def test_hallucination_is_not_in_the_quality_phase():
    """It needs the tool output in the prompt; the quality metrics must see the question
    the customer actually asked. One frame cannot serve both."""
    pytest.importorskip("vertexai", reason="google-cloud-aiplatform not installed")
    from vertexai._genai import types as t
    names = []
    for m in V.quality_metrics(t, "p", "us-central1"):
        try:
            names.append(getattr(m, "name", None))
        except Exception:
            pass
    assert "hallucination_v1" not in names
    assert "HALLUCINATION" not in names


def test_grounding_metric_uses_repeated_sampling():
    """Single-sample scored an unmissable fabrication 0.0, 0.0, 0.0, 1.0 over four runs."""
    pytest.importorskip("vertexai", reason="google-cloud-aiplatform not installed")
    from vertexai._genai import types as t
    metrics = V.grounding_metrics(t)
    assert len(metrics) == 1
    count = getattr(metrics[0], "judge_model_sampling_count", None)
    assert count == V.GROUNDING_SAMPLING and count > 1


def test_trace_frame_drops_cases_with_no_tool_call():
    """The filter must be on tool CALLS, not on events. A refusal still emits its final
    text event, so filtering on `intermediate_events` sent the three refusal cases to
    TOOL_USE_QUALITY, which rejected each with "requires tool calls in the evaluation
    trace" — and the metric still published a mean over whatever survived."""
    pytest.importorskip("pandas")
    rows = _rows()
    rows[0]["calls"] = []                     # declined, but still has an event
    rows[0]["intermediate_events"] = [{"author": "agent", "content": {"parts": []}}]
    assert V.trace_frame(rows) is None


def test_trace_frame_keeps_cases_that_did_call_a_tool():
    pytest.importorskip("pandas")
    df = V.trace_frame(_rows())
    assert df is not None and len(df) == 1


# --------------------------------------------------------------------------- #
# The refusal rubric is derived, not copied
# --------------------------------------------------------------------------- #
def test_refusal_rubric_covers_every_rule_in_the_policy_ssot():
    """A rubric that claims to test the refusal policy and has fallen a rule behind it is
    worse than no rubric, because it reports compliance. So the criteria are generated
    from `ANALYST_REFUSALS` and this asserts the derivation held."""
    pytest.importorskip("vertexai", reason="google-cloud-aiplatform not installed")
    from vertexai._genai import types as t
    metric = V.refusal_rubric(t, "p", "us-central1")
    rendered = metric.prompt_template
    for rule in V.ANALYST_REFUSALS["rules"]:
        assert rule["id"] in rendered, f"rule {rule['id']} missing from the rubric"
        assert rule["rule"] in rendered


def test_refusal_rubric_is_scored_as_compliance():
    pytest.importorskip("vertexai", reason="google-cloud-aiplatform not installed")
    from vertexai._genai import types as t
    metric = V.refusal_rubric(t, "my-proj", "us-central1")
    assert metric.name == "refusal_policy"
    # Pinned judge, same source as live_eval.py — not the SDK's default — and as a full
    # publisher resource name, which is the only form the service accepts here.
    assert metric.judge_model == (
        f"projects/my-proj/locations/us-central1/publishers/google/models/{V.JUDGE_MODEL}")
    assert metric.judge_model_sampling_count == V.REFUSAL_SAMPLING > 1


def test_refusal_rubric_asks_the_judge_for_json():
    """MetricPromptBuilder emits a PROSE template, and the service parses a custom metric's
    judge output as JSON — so every case failed with "Error parsing JSON ... Input: {##
    Evaluation" and the metric reported None while the judge had actually answered
    correctly. The template must request strict JSON."""
    pytest.importorskip("vertexai", reason="google-cloud-aiplatform not installed")
    from vertexai._genai import types as t
    tmpl = V.refusal_rubric(t, "p", "us-central1").prompt_template
    assert isinstance(tmpl, str)
    assert '"score"' in tmpl and '"explanation"' in tmpl
    assert "ONLY a JSON object" in tmpl
    # The placeholders the service substitutes.
    assert "{prompt}" in tmpl and "{response}" in tmpl


def test_autorater_resource_name_is_fully_qualified():
    """The SDK passes judge_model through to `autorater_model` verbatim, and a bare model
    id is rejected with "Invalid autorater model resource name" — observed as a 400 on
    every case, surfacing as `refusal_policy: None`."""
    got = V.autorater_resource("gemini-2.5-flash", "proj", "us-central1")
    assert got == "projects/proj/locations/us-central1/publishers/google/models/gemini-2.5-flash"


def test_an_already_qualified_name_is_left_alone():
    full = "projects/p/locations/l/publishers/google/models/m"
    assert V.autorater_resource(full, "other", "other") == full


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def test_a_gated_metric_that_did_not_compute_fails_the_run():
    """The fix for the worst fault in the first live run: refusal_policy returned None
    because every case 400'd, the gate was skipped as non-numeric, and the run exited 0
    claiming "gates passed"."""
    breaches, uncomputed, not_run = V.check_gates({"refusal_policy": None},
                                                  {"refusal_policy"})
    assert uncomputed == ["refusal_policy"] and not breaches and not not_run
    assert V.report_gates({"refusal_policy": None}, {"refusal_policy"}) == 1


def test_a_gated_metric_missing_entirely_after_being_requested_fails():
    assert V.report_gates({}, {"refusal_policy"}) == 1


def test_a_metric_this_run_did_not_request_passes_quietly():
    # `--metrics tools` never asks for the rubrics; that is not a failure.
    breaches, uncomputed, not_run = V.check_gates({"tool_name_match": 1.0}, {"tool_name_match"})
    assert not_run == ["refusal_policy"] and not uncomputed and not breaches
    assert V.report_gates({"tool_name_match": 1.0}, {"tool_name_match"}) == 0


def test_a_refusal_breach_fails_the_run():
    assert V.report_gates({"refusal_policy": 0.857}, {"refusal_policy"}) == 1


def test_full_compliance_passes():
    assert V.report_gates({"refusal_policy": 1.0}, {"refusal_policy"}) == 0


def test_only_the_categorical_metric_is_gated():
    """The tool metrics are deliberately NOT gated. Two things break an absolute threshold
    here: `get_transaction_history`'s `limit` is optional, so the parameter matchers
    penalise a model that correctly reads "last 3" off the question; and with 7 cases a
    single nondeterministic flip moves any mean by 0.143, which is coarser than any useful
    threshold (observed on consecutive runs against one revision). Baseline-relative drift
    detection on this dataset is canary_eval.py's job. A refusal breach is categorical, so
    it stays gated."""
    assert set(V.GATES) == {"refusal_policy"}
    assert V.GATES["refusal_policy"] == 1.00


# --------------------------------------------------------------------------- #
# No placeholders reach a metric
# --------------------------------------------------------------------------- #
def test_collect_drops_failed_cases_instead_of_filling_them(monkeypatch):
    cases = V.load_cases()

    def boom(agent_url, case, timeout=90.0):
        if case["id"] == "bal-1":
            return {"response": "ok", "trajectory": [], "tool_outputs": [],
                    "intermediate_events": [], "model_requested": "gemini-2.5-flash",
                    "model_served": "gemini-2.5-flash-002"}
        raise OSError("unreachable")

    monkeypatch.setattr(V, "ask_agent", boom)
    rows, meta = V.collect("https://agent.invalid", cases)
    assert [r["id"] for r in rows] == ["bal-1"]
    assert len(meta["failures"]) == len(cases) - 1
    assert meta["cases_scored"] == 1
    assert meta["agent_model_served"] == "gemini-2.5-flash-002"


def test_an_empty_answer_is_a_failure_not_a_zero_score(monkeypatch):
    monkeypatch.setattr(V, "ask_agent",
                        lambda url, case, timeout=90.0: {"response": ""})
    rows, meta = V.collect("https://agent.invalid", V.load_cases()[:1])
    assert rows == []
    assert meta["failures"][0]["error"] == "empty_response"


def test_collect_never_backfills_the_served_version(monkeypatch):
    monkeypatch.setattr(V, "ask_agent", lambda url, case, timeout=90.0: {
        "response": "ok", "model_requested": "gemini-2.5-flash", "model_served": None})
    _rows_, meta = V.collect("https://agent.invalid", V.load_cases()[:1])
    assert meta["agent_model_requested"] == "gemini-2.5-flash"
    assert meta["agent_model_served"] is None
