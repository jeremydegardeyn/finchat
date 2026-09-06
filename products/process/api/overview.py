"""The customer-overview capability, independent of how it is served (ADR-0030).

Split out of `main.py` so the rule can be reused without dragging in a web framework.
`main.py` is the HTTP wrapper; this is the capability. Two callers need it:

  * the process service itself, over HTTP; and
  * any experience API running without a deployed process service — the MCP server's
    demo mode loads this module directly rather than reimplementing `next_action`,
    which is the one thing that must not exist twice.

Nothing here imports FastAPI or pydantic, so it stays importable from a service whose
image does not ship them.
"""
from __future__ import annotations

from typing import Optional


class NextAction(dict):
    """A plain dict, so callers can serialise it without a model dependency."""

    def __init__(self, kind: str, label: str, reason: str):
        super().__init__(kind=kind, label=label, reason=reason)


def decide_next_action(balance: Optional[float], loans: list[dict],
                       recent: list[dict]) -> NextAction:
    """The single most useful thing this customer could do now.

    The one business rule in the platform that more than one channel needs, which is
    why it lives here rather than in whichever channel asked for it first.

    Ordered by what the customer most needs to know, not by severity. A pending loan
    decision outranks an overdraft because they can act on neither, but only one of
    them is news.

    A masked balance is deliberately NOT treated as zero. Column-level security returns
    NULL to a reader without fine-grained access (ADR-0019), and reading that as "no
    money" turns a policy outcome into a false alarm — the `masked_null` refusal rule,
    applied to a decision rather than to prose.
    """
    pending = [l for l in loans if l.get("status") == "PENDING_APPROVAL"]
    if pending:
        return NextAction("await_loan_decision",
                          "Your loan application is with an approver",
                          f"loan {pending[0].get('loan_id')} is PENDING_APPROVAL")

    decided = [l for l in loans if l.get("status") in ("APPROVED", "REJECTED")]
    if decided:
        latest = decided[0]
        return NextAction("review_loan_decision",
                          f"Your loan was {latest.get('status', '').lower()}",
                          f"loan {latest.get('loan_id')} has a final decision")

    if balance is None:
        return NextAction("none", "Nothing needs your attention",
                          "balance is masked at this access level, so no balance-based "
                          "advice is offered")

    if balance < 0:
        return NextAction("cover_overdraft", "Your balance is negative",
                          f"balance {balance} is below zero")

    if not recent:
        return NextAction("none", "Nothing needs your attention",
                          "no posted activity in the recent window")

    return NextAction("none", "Nothing needs your attention",
                      "no pending decision and the balance is positive")


def build_overview(account_id: str, backends, recent_limit: int = 5) -> dict:
    """Compose one customer view across the transactions and loan domains.

    Degrades per source rather than failing whole. A loan service that is down should
    not cost the customer their balance, so an unreachable source is named in `partial`
    and the view is returned without it — the channel can then say what is missing
    instead of showing a spinner or, worse, a confident but incomplete page.

    `backends` is passed in rather than imported so this stays testable and so a caller
    with a different transport (in-process demo, HTTP) can supply its own.
    """
    partial: list[str] = []

    bal = backends.balance(account_id)  # NotFound / SourceUnavailable propagate

    try:
        recent = backends.recent_transactions(account_id, recent_limit)
    except backends.SourceUnavailable:
        recent = []
        partial.append("recent_activity")

    try:
        loans = backends.loans_for_account(account_id)
    except backends.SourceUnavailable:
        loans = []
        partial.append("loans")

    return {
        "account_id": account_id,
        "currency": bal.get("currency", "USD"),
        "balance": bal.get("balance"),
        "last_activity_at": bal.get("last_activity_at"),
        "recent_activity": recent,
        "loans": loans,
        "next_action": dict(decide_next_action(bal.get("balance"), loans, recent)),
        "partial": partial,
    }
