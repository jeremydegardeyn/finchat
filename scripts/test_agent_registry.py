"""Tests for the agent registry gate (ADR-0023).

The point of these is not to test the catalogue's contents — it is to prove the gate
actually bites. A control that cannot be shown to fail is not a control.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents_catalog  # noqa: E402
import verify_agent_registry as v  # noqa: E402


# --- Source scanning ---------------------------------------------------------

def test_scans_adk_agents_and_their_tools(tmp_path: Path):
    src = tmp_path / "a.py"
    src.write_text(
        "from google.adk.agents import Agent\n"
        "x = Agent(name='alpha', model=M, tools=[tool_one, tool_two])\n"
        "y = Agent(name='beta', model=M, tools=[])\n",
        encoding="utf-8")
    assert v.scan_adk_agents(src) == {"alpha": ["tool_one", "tool_two"], "beta": []}


def test_sequential_agent_is_not_an_identity(tmp_path: Path):
    """SequentialAgent is control flow, not an actor — it must not demand registration."""
    src = tmp_path / "a.py"
    src.write_text(
        "from google.adk.agents import Agent, SequentialAgent\n"
        "p = SequentialAgent(name='pipeline', sub_agents=[a, b])\n"
        "a = Agent(name='alpha', tools=[t])\n",
        encoding="utf-8")
    assert set(v.scan_adk_agents(src)) == {"alpha"}


# --- The checks that matter ---------------------------------------------------

def _agent(**over) -> dict:
    base = dict(id="test_agent", display="Test", product="p", kind="llm_agent",
                runtime="cloud-run:x", source="products/loans/agents/agents.py",
                code_name="credit_agent", owner="o@x.com", business_area="BA",
                risk_tier="HIGH", sa_key="agent-test", model_alias="pinned-1",
                tools=["generate_credit_profile"], data_scope="scope",
                consequential=False, hitl=False, model_ref="M3",
                registered="2026-08-04", last_recertified="2026-08-04", status="active")
    base.update(over)
    return base


def test_drift3_catches_a_tool_added_in_code_but_not_registered():
    """The headline check: code grants a tool the registry never approved."""
    f = v.Findings()
    v.check_drift([_agent(tools=[])], f)  # code has generate_credit_profile, registry has none
    assert any(x.startswith("DRIFT-3") for x in f.failures)
    assert "generate_credit_profile" in " ".join(f.failures)


def test_drift3_passes_when_registry_matches_code():
    f = v.Findings()
    v.check_drift([_agent()], f)
    # DRIFT-1 is expected here (the source file holds four other loans agents this
    # single-entry registry doesn't cover); the assertion is scoped to tool drift.
    assert [x for x in f.failures if x.startswith("DRIFT-3")] == []


def test_drift1_catches_an_agent_that_exists_only_in_code():
    """Registering one loans agent must not satisfy the file's other four."""
    f = v.Findings()
    v.check_drift([_agent()], f)  # only credit_agent registered
    unregistered = [x for x in f.failures if x.startswith("DRIFT-1")]
    assert any("approval_agent" in x for x in unregistered)
    assert any("notification_agent" in x for x in unregistered)


def test_drift2_catches_a_registered_agent_missing_from_code():
    f = v.Findings()
    v.check_drift([_agent(code_name="ghost_agent")], f)
    assert any(x.startswith("DRIFT-2") and "ghost_agent" in x for x in f.failures)


# --- The live catalogue -------------------------------------------------------

def test_live_registry_is_clean():
    """The committed registry must match the code it claims to describe.

    Completeness, distinct identity, the human-in-the-loop requirement and
    recertification are asserted against the same registry by
    policy/registry/registry.rego, in the CI step that runs conftest (ADR-0027).
    """
    registry = [a for a in agents_catalog.agents("dev") if a["status"] == "active"]
    f = v.Findings()
    v.check_drift(registry, f)
    v.check_coverage(registry, f)
    assert f.failures == [], "\n".join(f.failures)


def test_policy_input_carries_what_the_registry_policy_judges():
    """The Rego rules are only as good as the document they are handed.

    `emit_tfvars` is deliberately minimal, so it would be an easy and silent mistake to
    point conftest at it and quietly stop evaluating the fields Terraform never needed.
    """
    doc = agents_catalog.policy_input("prod", today=date(2026, 8, 4))
    assert doc["today"] == "2026-08-04"
    assert doc["agents"], "policy input has no agents"
    for a in doc["agents"]:
        for field in ("owner", "business_area", "risk_tier", "sa_key", "data_scope",
                      "consequential", "hitl", "last_recertified", "recert_due",
                      "service_account"):
            assert field in a, f"{a['id']} policy input is missing {field}"
        assert a["recert_due"] == agents_catalog.recert_due(a).isoformat()


def test_every_agent_has_a_distinct_service_account():
    registry = agents_catalog.agents("dev")
    sa_ids = [agents_catalog.service_account_id(a, "dev") for a in registry]
    assert len(sa_ids) == len(set(sa_ids)), "service account ids collide after truncation"


def test_service_account_ids_are_valid_for_iam():
    """IAM: 6-30 chars, lowercase letters/digits/hyphens, must start with a letter."""
    import re
    for a in agents_catalog.agents("dev"):
        sa = agents_catalog.service_account_id(a, "dev")
        assert re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", sa), f"invalid SA id: {sa}"


def test_every_agent_maps_to_a_model_inventory_row():
    """The agent registry and the model inventory must not drift apart."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "19-model-inventory.md").read_text(
        encoding="utf-8")
    for a in agents_catalog.agents("dev"):
        assert f"| {a['model_ref']} |" in doc, \
            f"{a['id']} references {a['model_ref']}, absent from docs/19"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# --- DRIFT-4: gateway coverage (ADR-0026) ------------------------------------
# The gate exists because screening coverage cannot be maintained by instrumenting each
# agent: `armor` sits in one place while five call sites reach Vertex. Routing everything
# through the gateway and asserting it here is the form that scales.

def _models(tmp_path: Path, body: str) -> dict:
    src = tmp_path / "agents.py"
    src.write_text("from google.adk.agents import Agent\n" + body, encoding="utf-8")
    return v.scan_agent_models(src)


def test_bare_model_string_is_flagged_as_direct(tmp_path: Path):
    got = _models(tmp_path, 'a = Agent(name="solo", model="gemini-2.5-flash")\n')
    assert got["solo"] == "direct"


def test_inline_gateway_call_counts_as_transit(tmp_path: Path):
    got = _models(tmp_path,
                  'from gateway_llm import gateway_model\n'
                  'a = Agent(name="wrapped", model=gateway_model("wrapped", "m"))\n')
    assert got["wrapped"] == "gateway"


def test_local_lambda_alias_for_the_gateway_is_resolved(tmp_path: Path):
    """The real shape in products/loans/agents/agents.py. An earlier version of this
    scanner matched only the bare name and reported all five loan agents as bypassing the
    gateway — a false failure against code doing exactly the right thing."""
    got = _models(tmp_path,
                  'from gateway_llm import gateway_model\n'
                  '_m = lambda agent_id: gateway_model(agent_id, "m", owner="o")\n'
                  'a = Agent(name="aliased", model=_m("aliased"))\n')
    assert got["aliased"] == "gateway"


def test_local_def_wrapping_the_gateway_is_resolved(tmp_path: Path):
    got = _models(tmp_path,
                  'from gateway_llm import gateway_model\n'
                  'def _mk(i):\n    return gateway_model(i, "m")\n'
                  'a = Agent(name="viadef", model=_mk("viadef"))\n')
    assert got["viadef"] == "gateway"


def test_unrelated_local_call_is_not_mistaken_for_the_gateway(tmp_path: Path):
    """Resolving aliases must not become 'any call counts', or the gate stops biting."""
    got = _models(tmp_path,
                  'def pick(i):\n    return "gemini-2.5-flash"\n'
                  'a = Agent(name="sneaky", model=pick("sneaky"))\n')
    assert got["sneaky"] == "direct"


def test_unresolvable_name_reports_rather_than_fails(tmp_path: Path):
    """A false failure trains people to disable the check, so indirection is INFO."""
    got = _models(tmp_path,
                  'from gateway_llm import gateway_model\n'
                  'M = gateway_model("x", "m")\n'
                  'a = Agent(name="vianame", model=M)\n')
    assert got["vianame"] in ("gateway", "indirect")


def test_the_real_repo_has_no_agent_bypassing_the_gateway():
    """The assertion that actually matters: no registered agent reaches Vertex directly."""
    f = v.Findings()
    v.check_coverage([a for a in agents_catalog.agents("prod") if a["status"] == "active"], f)
    assert [x for x in f.failures if x.startswith("DRIFT-4")] == []


def test_drift4_fires_when_an_agent_does_bypass(tmp_path: Path, monkeypatch):
    """Proof the gate bites — a control that cannot be shown to fail is not a control."""
    src = tmp_path / "bypass.py"
    src.write_text('from google.adk.agents import Agent\n'
                   'a = Agent(name="rogue", model="gemini-2.5-flash")\n', encoding="utf-8")
    monkeypatch.setattr(v, "REPO", tmp_path)
    f = v.Findings()
    v.check_coverage([{"id": "rogue", "code_name": "rogue", "source": "bypass.py",
                       "kind": "adk_agent"}], f)
    assert any(x.startswith("DRIFT-4") for x in f.failures)
