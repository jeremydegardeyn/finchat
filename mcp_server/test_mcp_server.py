"""Offline tests for the MCP surface (ADR-0027). No GCP, no network.

Two of these are negative tests, and they are the ones that matter. A tool surface
is a permission grant: what it *omits* is as much a decision as what it exposes, and
an omission with no test decays into an oversight the first time someone adds a tool
by copying the one above it.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load(**env):
    """Import the server with a specific configuration, from scratch."""
    for key in ("FINCHAT_MCP_PERSONA", "FINCHAT_MCP_ALLOW_WRITES",
                "FINCHAT_TXN_API_URL", "FINCHAT_LOAN_API_URL", "FINCHAT_AGENT_URL"):
        os.environ.pop(key, None)
    os.environ.update(env)
    for mod in ("server", "backends", "knowledge"):
        if mod in sys.modules:
            del sys.modules[mod]
    return importlib.import_module("server")


def _tools(srv) -> set[str]:
    return {t.name for t in asyncio.run(srv.mcp.list_tools())}


# --- what is offered ---------------------------------------------------------
def test_default_persona_gets_reads_only():
    names = _tools(_load())
    assert {"get_account_balance", "get_account_transactions", "get_account_summary",
            "get_loan_status", "describe_data_model"} <= names
    assert "submit_loan_application" not in names, "writes must be opt-in"
    assert "list_loans" not in names, "approver tools must not reach a customer scope"


def test_writes_are_opt_in():
    assert "submit_loan_application" in _tools(_load(FINCHAT_MCP_ALLOW_WRITES="1"))


def test_approver_scope_adds_the_audit_surface():
    names = _tools(_load(FINCHAT_MCP_PERSONA="approver"))
    assert {"list_loans", "get_loan_audit"} <= names


def test_the_decision_endpoint_is_never_exposed():
    """The human-in-the-loop approval is not an agent-callable operation.

    `POST /v1/loans/{id}/decision` exists on the loan API and is deliberately absent
    from every persona here. If a tool ever appears that records a decision, the
    workflow's whole reason for existing has been removed by accident.
    """
    for env in ({}, {"FINCHAT_MCP_ALLOW_WRITES": "1"},
                {"FINCHAT_MCP_PERSONA": "approver", "FINCHAT_MCP_ALLOW_WRITES": "1"}):
        names = _tools(_load(**env))
        assert not any("decision" in n or "approve" in n or "reject" in n for n in names), \
            f"a decision-recording tool appeared under {env}"


# --- the knowledge plane travels with the tools ------------------------------
def test_instructions_carry_the_refusal_policy():
    srv = _load()
    for fragment in ("financial, tax, investment or legal advice",
                     "isn't modelled in our data yet",
                     "outside the analytics perimeter",
                     "masked at your access level"):
        assert fragment in srv._INSTRUCTIONS, f"refusal rule missing: {fragment}"


def test_perimeter_resource_is_the_compiled_ssot_not_a_copy():
    srv = _load()
    import knowledge

    assert knowledge.perimeter() == dict(knowledge.okf().ANALYST_PERIMETER)
    assert srv.r_perimeter().strip().startswith("{")
    assert "graph" in knowledge.perimeter()


def test_resources_and_prompt_are_published():
    srv = _load()
    uris = {str(r.uri) for r in asyncio.run(srv.mcp.list_resources())}
    assert {"finchat://knowledge/data-model", "finchat://knowledge/perimeter",
            "finchat://knowledge/joins", "finchat://knowledge/refusals"} <= uris
    templates = {t.uriTemplate for t in asyncio.run(srv.mcp.list_resource_templates())}
    assert "finchat://contracts/{name}" in templates
    assert {p.name for p in asyncio.run(srv.mcp.list_prompts())} == {"finchat_analyst"}


@pytest.mark.parametrize("query,expect", [
    ("what does revenue mean", "net-revenue"),
    ("do pending transactions count", "posted-transaction"),
    ("how is overdraft calculated", "overdraft"),
    ("can I get a household rollup", "household"),
])
def test_describe_data_model_finds_the_governing_section(query, expect):
    srv = _load()
    assert expect in srv.describe_data_model(query).lower()


@pytest.mark.parametrize("query,expect", [
    ("what time does the lakewood branch open", "Lakewood"),
    ("44107", "Lakewood"),          # exact token: the case dense retrieval misses
    ("what are the overdraft fees", "verdraft"),
    ("which ATMs are free", "ATM"),
])
def test_the_offline_kb_answers_from_the_real_corpus(query, expect):
    """The local fallback is BM25 over the shipped corpus, not a stub.

    The zip-code case is the point: an exact token with no semantic neighbours is
    precisely what the sparse arm exists for (docs/21), and it is what a customer
    actually types.
    """
    srv = _load()
    out = srv.search_knowledge_base(query)
    assert expect.lower() in out.lower()
    assert '"retriever": "sparse-local"' in out, "the weaker path must label itself"


def test_the_kb_tool_is_offered_to_every_persona():
    """Policy and branch questions are the most common customer questions there are;
    gating them behind a persona would make the default surface nearly useless."""
    for env in ({}, {"FINCHAT_MCP_PERSONA": "approver"}):
        assert "search_knowledge_base" in _tools(_load(**env))


def test_kb_routes_to_the_agent_when_one_is_configured():
    srv = _load(FINCHAT_AGENT_URL="https://agent.example.invalid/")
    assert srv.backends.mode["knowledge_base"] == "agent"
    assert _load().backends.mode["knowledge_base"] == "local-bm25"


@pytest.mark.parametrize("status,fragment", [
    (403, "gcloud auth login"),
    (401, "run.invoker"),
    (404, "Redeploy the agent"),
    (500, "returned 500"),
    (None, "could not be reached"),
])
def test_every_agent_failure_degrades_and_names_the_cause(monkeypatch, status, fragment):
    """Configuring the server harder must not make it answer less.

    Pointing at a real agent that then refuses used to turn a working branch-hours
    question into a bare error — worse than the demo-mode behaviour it replaced.
    """
    srv = _load(FINCHAT_AGENT_URL="https://agent.example.invalid/")
    import backends

    def boom(*a, **k):
        raise backends.BackendError("simulated", status=status)

    monkeypatch.setattr(backends, "_request", boom)
    out = srv.search_knowledge_base("what time does the lakewood branch open")
    assert "Degraded to local BM25" in out
    assert fragment in out
    assert "Lakewood" in out, "the fallback must still answer the question"
    assert '"retriever": "sparse-local"' in out


def test_account_tools_never_degrade_to_demo_data(monkeypatch):
    """The contrast that makes the KB's fallback safe.

    The KB corpus is public policy text either way, so degrading costs ranking
    quality. A balance has no such equivalence: demo figures presented as a
    customer's real ones is worse than any error, so these fail loudly.
    """
    srv = _load(FINCHAT_TXN_API_URL="https://txn.example.invalid/")
    import backends

    def boom(*a, **k):
        raise backends.BackendError("simulated", status=403)

    monkeypatch.setattr(backends, "_request", boom)
    out = srv.get_account_balance("acct-001")
    assert out.startswith("FinChat error:")
    assert "balance" not in out.lower(), "a demo balance must never surface as real"


def test_an_empty_kb_result_tells_the_model_not_to_improvise():
    """A miss must not read as an invitation to answer from general knowledge —
    these are one bank's policies, not an industry norm."""
    srv = _load()
    out = srv.search_knowledge_base("zzzq wugbrl nonexistent")
    assert "don't have that information" in out


def test_unmodelled_terms_answer_with_the_owner_not_a_number():
    srv = _load()
    out = srv.lookup_glossary_term("household")
    assert "modelled" in out and "false" in out.lower()
    assert "owner" in out


# --- the demo path -----------------------------------------------------------
def test_demo_backends_serve_the_apis_own_sample_data():
    srv = _load()
    assert srv.backends.mode["transactions"] == "demo"
    accounts = srv.list_sample_accounts(3)
    assert "acct-001" in accounts
    assert '"balance"' in srv.get_account_balance("acct-001")
    assert '"net_balance"' in srv.get_account_summary("acct-001")


def test_a_missing_account_reports_rather_than_raises():
    """Tool errors reach the model as text; an exception would surface as a
    protocol error the model cannot reason about or recover from."""
    srv = _load()
    out = srv.get_account_balance("acct-does-not-exist")
    assert out.startswith("FinChat error:")
    assert "not found" in out


def test_http_mode_is_selected_by_configuration_not_by_flag():
    srv = _load(FINCHAT_TXN_API_URL="https://txn.example.invalid/")
    assert srv.backends.mode["transactions"] == "http"
    assert srv.backends.mode["txn_api_url"] == "https://txn.example.invalid"
    assert srv.backends.mode["loans"] == "demo"
