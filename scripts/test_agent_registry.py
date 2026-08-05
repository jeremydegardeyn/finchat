"""Tests for the agent registry gate (ADR-0023).

The point of these is not to test the catalogue's contents — it is to prove the gate
actually bites. A control that cannot be shown to fail is not a control.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
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


def test_reg2_rejects_a_shared_service_account():
    f = v.Findings()
    v.check_registration([_agent(id="a1"), _agent(id="a2")], f)  # same sa_key
    assert any(x.startswith("REG-2") for x in f.failures)


def test_reg3_rejects_consequential_action_without_a_human_gate():
    f = v.Findings()
    v.check_registration([_agent(consequential=True, hitl=False)], f)
    assert any(x.startswith("REG-3") for x in f.failures)


def test_reg1_rejects_an_unowned_agent():
    f = v.Findings()
    v.check_registration([_agent(owner="")], f)
    assert any(x.startswith("REG-1") and "owner" in x for x in f.failures)


def test_life1_fails_an_overdue_recertification():
    stale = (date(2026, 8, 4) - timedelta(days=200)).isoformat()
    f = v.Findings()
    v.check_lifecycle([_agent(last_recertified=stale)], f, today=date(2026, 8, 4))
    assert any(x.startswith("LIFE-1") for x in f.failures)


def test_life1_warns_before_it_fails():
    """A recert due inside 14 days warns; it must not break the build yet."""
    soon = (date(2026, 8, 4) - timedelta(days=80)).isoformat()  # HIGH = 90d cycle
    f = v.Findings()
    v.check_lifecycle([_agent(last_recertified=soon)], f, today=date(2026, 8, 4))
    assert f.failures == []
    assert any(x.startswith("LIFE-1") for x in f.warnings)


# --- The live catalogue -------------------------------------------------------

def test_live_registry_is_clean():
    """The committed registry must pass every failing check."""
    registry = [a for a in agents_catalog.agents("dev") if a["status"] == "active"]
    f = v.Findings()
    v.check_drift(registry, f)
    v.check_registration(registry, f)
    v.check_lifecycle(registry, f, today=date(2026, 8, 4))
    assert f.failures == [], "\n".join(f.failures)


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
