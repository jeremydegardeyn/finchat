"""Offline tests for the process layer. No GCP, no network.

The next-action rule is the reason this service exists, so it gets the most attention:
it is the piece that would otherwise be reimplemented per channel and drift.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_module(name, filename):
    """Import this service's module under a unique name.

    Every service in this repo names its app module `main.py`, so `import main` in a
    whole-repo pytest run resolves to whichever service was imported first. CI runs each
    suite with its own working-directory and would never see it; running the suite from
    the repo root does. Loading by path keeps the module addressable by where it lives.
    """
    import importlib.util
    import sys

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import sources as backends  # noqa: E402

main = _load_module("finchat_process_main", "main.py")
capability = _load_module("finchat_process_capability", "overview.py")
decide_next_action = capability.decide_next_action


# --- the business rule -------------------------------------------------------
def test_a_pending_decision_outranks_an_overdraft():
    """Ordered by what the customer most needs to know, not by severity.

    Both are true at once here. The pending decision wins because the customer can act
    on neither, and only one of them is news.
    """
    a = decide_next_action(-500.0, [{"loan_id": "l1", "status": "PENDING_APPROVAL"}], [])
    assert a["kind"] == "await_loan_decision"
    assert "l1" in a["reason"]


def test_a_decided_loan_surfaces_after_pending_ones():
    a = decide_next_action(100.0, [{"loan_id": "l9", "status": "APPROVED"}], [])
    assert a["kind"] == "review_loan_decision"
    assert "approved" in a["label"].lower()


def test_a_negative_balance_is_flagged():
    a = decide_next_action(-12.5, [], [{"transaction_id": "t"}])
    assert a["kind"] == "cover_overdraft"


def test_a_masked_balance_is_not_treated_as_zero():
    """The rule this service would most plausibly get wrong.

    Column-level security returns NULL to a reader without fine-grained access
    (ADR-0019). Reading that as "no money" turns a policy outcome into a false alarm —
    the masked_null refusal rule, applied to a decision instead of to prose.
    """
    a = decide_next_action(None, [], [])
    assert a["kind"] == "none"
    assert "masked" in a["reason"]


def test_a_healthy_account_gets_no_manufactured_action():
    a = decide_next_action(250.0, [], [{"transaction_id": "t"}])
    assert a["kind"] == "none"


# --- composition over the demo repositories ----------------------------------
def test_the_overview_composes_both_domains_offline():
    o = capability.build_overview("acct-001", backends)
    assert o["account_id"] == "acct-001"
    assert o["currency"]
    assert isinstance(o["recent_activity"], list)
    assert o["next_action"]["kind"]
    assert o["partial"] == []


def test_a_missing_account_is_a_404_not_an_empty_screen():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        main.customer_overview("acct-does-not-exist")
    assert e.value.status_code == 404


def test_one_dead_source_degrades_that_section_not_the_view(monkeypatch):
    """A loan service outage must not cost the customer their balance.

    The alternative — failing the whole view — is what makes a channel show a spinner
    where a balance should be, and it is a worse outcome than a named gap.
    """
    def boom(*a, **k):
        raise backends.SourceUnavailable("simulated")

    monkeypatch.setattr(backends, "loans_for_account", boom)
    o = capability.build_overview("acct-001", backends)
    assert o["balance"] is not None
    assert o["loans"] == []
    assert "loans" in o["partial"]


# --- the layering itself -----------------------------------------------------
def test_loans_are_selected_by_the_system_api_not_filtered_here():
    """Selection belongs to the system that owns the rows.

    Pinned because filtering in Python would pass every other test in this file while
    quietly moving 200 records over the wire to keep two.
    """
    src = open(os.path.join(os.path.dirname(__file__), "sources.py"),
               encoding="utf-8").read()
    assert '"account_id": account_id' in src or "{\"account_id\": account_id}" in src, \
        "loans_for_account must pass account_id to the loan API"


def test_demo_modules_resolve_under_the_image_layout_too(tmp_path, monkeypatch):
    """The bug the Dockerfile hides until the first request.

    In the repo this file is four levels below the root; in the image it is at /app with
    the copied modules beside it. A fixed number of `.parent` walks is correct in exactly
    one of those, and the wrong one climbs out of /app — a build that goes green and a
    service that fails on its first demo call.
    """
    (tmp_path / "products" / "loans" / "api").mkdir(parents=True)
    target = tmp_path / "products" / "loans" / "api" / "store.py"
    target.write_text("", encoding="utf-8")

    monkeypatch.setattr(backends, "SEARCH_ROOTS", (tmp_path, backends.REPO_ROOT))
    assert backends._resolve("products", "loans", "api", "store.py") == target


def test_an_unresolvable_module_points_at_the_real_fix():
    """The demo path is checkout-only; in a container the answer is to set the URLs.

    Worth a specific message rather than a bare importlib FileNotFoundError, because the
    reader hitting it is most likely running the image and wondering what is missing —
    and the answer is configuration, not a file.
    """
    with pytest.raises(FileNotFoundError) as e:
        backends._resolve("products", "nope", "missing.py")
    assert "TXN_API_URL" in str(e.value)


def test_the_rule_lives_outside_the_web_framework():
    """`overview.py` must stay importable without FastAPI.

    The MCP server reuses this capability in demo mode and its image does not ship a
    web framework. If the rule drifts back into `main.py`, the alternative for that
    caller is a second copy of next_action — the one thing this layer exists to prevent.
    """
    import ast

    tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "overview.py"),
                          encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    # Read imports rather than source text: the module docstring names both frameworks
    # deliberately, and a check that fires on the explanation gets fixed by deleting it.
    assert not ({"fastapi", "pydantic"} & imported), imported

    wrapper = open(os.path.join(os.path.dirname(__file__), "main.py"), encoding="utf-8").read()
    assert "def decide_next_action" not in wrapper,         "the rule belongs in overview.py; main.py is transport"


# --- the deep health check ---------------------------------------------------
# `/healthz` reported "ok" for four weeks while the loan filter 400'd on every call,
# because this layer degrades per source and a degraded answer still looks complete.
# These pin the two properties that make the deep check worth having.
class _Sources:
    """A stand-in for the system APIs, with one of them optionally broken."""

    # The real exception types, because `main` catches `backends.SourceUnavailable` and
    # a stub without them turns a 503 assertion into an AttributeError.
    SourceUnavailable = backends.SourceUnavailable
    NotFound = backends.NotFound

    def __init__(self, loans_ok=True):
        self.loans_ok = loans_ok

    def mode(self):
        return {"transactions": "demo", "loans": "demo"}

    def sample_account(self):
        return "acct-health-1"

    def balance(self, account_id):
        return {"balance": 2490.81, "currency": "USD",
                "last_activity_at": "2026-06-07T19:27:53Z"}

    def recent_transactions(self, account_id, limit):
        return [{"transaction_id": "txn-secret-42", "txn_type": "DEPOSIT",
                 "amount": 2633.03, "currency": "USD", "status": "POSTED",
                 "event_time": "2026-06-07T19:27:53Z"}]

    def loans_for_account(self, account_id):
        if not self.loans_ok:
            raise backends.SourceUnavailable("/v1/loans -> 400")
        return []


def _deep(sources):
    original = main.backends
    main.backends = sources
    try:
        return main.healthz_deep()
    finally:
        main.backends = original


def test_the_deep_check_fails_when_a_source_is_degraded():
    """The whole point. A 200 here is what let a broken join pass for four weeks."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _deep(_Sources(loans_ok=False))
    assert excinfo.value.status_code == 503
    assert "loans" in excinfo.value.detail["degraded"]


def test_the_deep_check_passes_when_every_source_answers():
    """The other half: it must not be a check that always fails, which is the same as
    a check nobody reads. Zero loans is legitimate and must stay a pass."""
    body = _deep(_Sources(loans_ok=True))
    assert body["status"] == "ok"
    assert body["degraded"] == []
    assert body["shape"]["loan_rows"] == 0


def test_the_deep_check_emits_no_customer_data():
    """A monitoring endpoint that returns balances turns every log sink, uptime-check
    history and CI console into a place customer data now lives. Booleans and counts
    only — asserted structurally, because the drift here would be one 'useful' field
    at a time and each one would look defensible on its own.
    """
    import json

    body = json.dumps(_deep(_Sources(loans_ok=True)))
    for leaked in ("2490.81", "2633.03", "acct-health-1", "txn-secret-42",
                   "2026-06-07"):
        assert leaked not in body, f"deep check leaked {leaked!r}"
