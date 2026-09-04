"""FinChat MCP server — the platform's capabilities as a tool surface (ADR-0027).

MCP is the *agent channel's* interface to FinChat. It is deliberately not a new
way into the data: every tool here calls the same governed DaaS and loan APIs the
web UI calls, so the query caps, Gold-only serving rule, per-user column-level
security and audit trail hold identically whichever client is on the other end.
The protocol changes who can call, not what the platform allows.

Three things are published, and the distinction is the design:

- **Tools** — the governed operations. Reads by default; the one write is off
  unless explicitly enabled, and the human-in-the-loop decision endpoint is not
  exposed at all (see `docs/27-mcp-service.md` for why).
- **Resources** — the knowledge plane: perimeter, join paths, glossary, refusal
  rules, data contracts, the ontology. Compiled from the same SSOT as the analyst
  agent's grounding, so a third-party client and our own agent cannot disagree
  about what a metric means.
- **A prompt** — the analyst framing, so a client that supports prompts starts
  inside the perimeter instead of guessing its way there.

Transports: `stdio` for a desktop client on a laptop, `streamable-http` for
service-to-service use on GCP. Same tool definitions either way; only the identity
mechanism differs.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import knowledge  # noqa: E402
from backends import Backends  # noqa: E402

# --- configuration -----------------------------------------------------------
# The persona a client is scoped to. This selects which tools are *offered*; it is
# not the access control. Real enforcement stays where it can't be edited by the
# caller: IAM on the private Cloud Run services, column-level security evaluated
# against the end user in BigQuery (ADR-0019), and the approver check inside the
# loan API. Treat this as the principle of least astonishment, not least privilege.
PERSONA = os.getenv("FINCHAT_MCP_PERSONA", "customer").strip().lower()
ALLOW_WRITES = os.getenv("FINCHAT_MCP_ALLOW_WRITES", "").lower() in ("1", "true", "yes")

_INSTRUCTIONS = f"""\
FinChat is a retail-banking Data & AI platform. This server exposes its governed
data products: banking transactions (balances, history, account summaries) and
loan applications (submission, status, decision audit trail).

Read `finchat://knowledge/data-model` before composing anything analytical. The
sign convention, the POSTED-only rule and the account bridge between a transaction
and a customer are all counter-intuitive if guessed at, and all documented there.

Behavioural rules this platform enforces on every agent surface, including this one:

{knowledge.refusal_bullets()}
These are not style preferences. They are the platform's stated refusal policy
(`knowledge/playbooks/refusal-escalation.md`); a client that ignores them produces
answers the bank cannot stand behind.
"""

mcp = FastMCP(
    "finchat",
    instructions=_INSTRUCTIONS,
    host=os.getenv("HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "8080")),
)

backends = Backends()

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                         openWorldHint=False)


def _err(exc: Exception) -> str:
    return f"FinChat error: {exc}"


# --- platform ----------------------------------------------------------------
@mcp.tool(annotations=_READ)
def finchat_status() -> str:
    """Report how this server is configured: data source, persona scope, writes.

    Worth calling first when a result looks unexpectedly synthetic — a server with
    no API URLs configured is serving the demo repository, not the bank's data.
    """
    return json.dumps({
        "persona": PERSONA,
        "writes_enabled": ALLOW_WRITES,
        "backends": backends.mode,
        "knowledge_sections": len(knowledge.section_names()),
    }, indent=2)


# --- transactions (Data Product 1) -------------------------------------------
@mcp.tool(annotations=_READ)
def list_sample_accounts(n: int = 5) -> str:
    """List account ids that have activity, to start an exploration.

    Account ids are opaque; there is no lookup from a customer name, by design —
    names are PII_DIRECT and structurally absent from these surfaces.
    """
    try:
        return json.dumps({"account_ids": backends.sample_accounts(n)}, indent=2)
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def get_account_balance(account_id: str) -> str:
    """Current balance for an account.

    The signed sum of POSTED transactions only: DEPOSIT adds; WITHDRAWAL, FEE and
    TRANSFER subtract. PENDING and REJECTED rows are excluded, so this will not
    match a naive sum over the transaction list.
    """
    try:
        return json.dumps(backends.balance(account_id), indent=2, default=str)
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def get_account_transactions(account_id: str, limit: int = 50) -> str:
    """Transaction history for an account, newest first.

    Includes PENDING and REJECTED rows. Filter to status POSTED before computing
    any total, or the number will not tie to the balance.
    """
    try:
        return json.dumps(backends.transactions(account_id, limit), indent=2, default=str)
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def get_recent_activity(account_id: str, days: int = 30) -> str:
    """Transactions on an account within a trailing window of days."""
    try:
        return json.dumps(backends.activity(account_id, days), indent=2, default=str)
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READ)
def get_account_summary(account_id: str) -> str:
    """Account summary from the Gold serving view: counts by type and net balance."""
    try:
        return json.dumps(backends.summary(account_id), indent=2, default=str)
    except Exception as e:
        return _err(e)


# --- loans (Data Product 2) --------------------------------------------------
@mcp.tool(annotations=_READ)
def get_loan_status(loan_id: str) -> str:
    """Current state of a loan application, including risk score and reason codes.

    `risk_score` runs 0 (best) to 100 (worst). A recommendation is advisory — the
    decision is a human's, recorded separately.
    """
    try:
        return json.dumps(backends.loan(loan_id), indent=2, default=str)
    except Exception as e:
        return _err(e)


if ALLOW_WRITES:
    @mcp.tool(annotations=_WRITE)
    def submit_loan_application(customer_name: str, amount: float, term_months: int,
                                account_id: Optional[str] = None) -> str:
        """Submit a loan application and return its id, risk score and reason codes.

        This creates a record a human will act on. Confirm the amount and term with
        the user before calling; do not infer them from context.

        Supplying `account_id` lets risk scoring read that account's overdraft
        history, which materially changes the score — a cross-product read that is
        documented lineage, not a side effect.
        """
        try:
            return json.dumps(
                backends.submit_loan(customer_name, amount, term_months, account_id),
                indent=2, default=str)
        except Exception as e:
            return _err(e)


if PERSONA == "approver":
    @mcp.tool(annotations=_READ)
    def list_loans(status: Optional[str] = None) -> str:
        """List loan applications, optionally filtered by workflow status.

        Statuses: CREATED, PROFILED, REVIEWED, RECOMMENDED, PENDING_APPROVAL,
        APPROVED, REJECTED, MODIFIED. Only the last four are business outcomes;
        the rest are workflow mechanics and should not be reported as decisions.
        """
        try:
            return json.dumps(backends.loans(status), indent=2, default=str)
        except Exception as e:
            return _err(e)

    @mcp.tool(annotations=_READ)
    def get_loan_audit(loan_id: str) -> str:
        """The append-only audit trail for a loan: every actor, action and decision.

        Decisions are versioned and never mutated, so this is the full history, not
        the current state. `get_loan_status` gives the latter.
        """
        try:
            return json.dumps(backends.loan_audit(loan_id), indent=2, default=str)
        except Exception as e:
            return _err(e)


# --- knowledge plane ---------------------------------------------------------
@mcp.tool(annotations=_READ)
def search_knowledge_base(query: str) -> str:
    """Search the bank's policies, fees, terms, lending info and branch hours.

    Returns source passages, not an answer — ground your reply in them and say so
    if they don't cover the question. Each carries a `retriever` field showing
    which arm found it (`hybrid`/`dense`/`sparse`, or `sparse-local` when this
    server is answering offline).

    This is the *product and policy* knowledge base. For what a column, metric or
    join means, use `describe_data_model` — a different corpus with a different owner.
    """
    try:
        results = backends.search_kb(query)
    except Exception as e:
        return _err(e)
    if not results:
        return (f"No knowledge-base passage matched {query!r}. Say you don't have that "
                f"information rather than answering from general knowledge — this is a "
                f"specific bank's policy, not an industry norm.")
    return json.dumps(results, indent=2, default=str)


@mcp.tool(annotations=_READ)
def describe_data_model(query: str, limit: int = 3) -> str:
    """Search the certified data model for how a concept is defined here.

    Covers tables, serving views, metrics, the property graph, code sets and known
    limitations. Use it before answering anything definitional — "revenue",
    "active customer" and "overdraft" all mean something specific in this platform
    and something else in general usage.
    """
    hits = knowledge.search_sections(query, limit)
    if not hits:
        return (f"No section matched {query!r}. Available sections: "
                f"{', '.join(knowledge.section_names())}")
    return "\n\n---\n\n".join(f"### {h['section']}\n{h['content']}" for h in hits)


@mcp.tool(annotations=_READ)
def lookup_glossary_term(term: str) -> str:
    """The certified definition of a business term, with its owner and review date.

    Some terms are certified as *not modelled* — Household is the standing example.
    For those the correct answer is to say so and name the owner, not to compute a
    plausible substitute.
    """
    entry = knowledge.find_term(term)
    if not entry:
        known = ", ".join(e.get("term", "") for e in knowledge.glossary())
        return f"{term!r} is not a certified term. Certified terms: {known}"
    return json.dumps(entry, indent=2, default=str)


# --- resources ---------------------------------------------------------------
@mcp.resource("finchat://knowledge/data-model", mime_type="text/markdown",
              description="Certified tables, views, metrics, graph and code sets.")
def r_data_model() -> str:
    return knowledge.okf().ANALYST_KNOWLEDGE


@mcp.resource("finchat://knowledge/perimeter", mime_type="application/json",
              description="Datasets and tables an analyst surface may reach.")
def r_perimeter() -> str:
    return json.dumps(knowledge.perimeter(), indent=2)


@mcp.resource("finchat://knowledge/joins", mime_type="text/markdown",
              description="Canonical join paths, generated from the ontology.")
def r_joins() -> str:
    return knowledge.join_bullets()


@mcp.resource("finchat://knowledge/glossary", mime_type="application/json",
              description="Certified business terms with owners and review dates.")
def r_glossary() -> str:
    return json.dumps(knowledge.glossary(), indent=2, default=str)


@mcp.resource("finchat://knowledge/refusals", mime_type="application/json",
              description="The platform's refusal and escalation rules.")
def r_refusals() -> str:
    return json.dumps(knowledge.refusal_rules(), indent=2, default=str)


@mcp.resource("finchat://knowledge/stewardship", mime_type="application/json",
              description="Owner and steward per concept, for escalation.")
def r_stewardship() -> str:
    return json.dumps(knowledge.stewardship(), indent=2, default=str)


@mcp.resource("finchat://ontology", mime_type="text/yaml",
              description="The conceptual SSOT: classes, relationships, metrics, axioms.")
def r_ontology() -> str:
    return knowledge.ontology_yaml() or "ontology.yaml not found"


@mcp.resource("finchat://contracts/{name}", mime_type="text/yaml",
              description="A published data contract for one data product.")
def r_contract(name: str) -> str:
    body = knowledge.contract(name)
    if body is None:
        return f"No contract {name!r}. Published: {', '.join(knowledge.contracts())}"
    return body


# --- prompt ------------------------------------------------------------------
@mcp.prompt(description="Analyse FinChat data inside the governed perimeter.")
def finchat_analyst(question: str) -> str:
    """Frame a question with the perimeter, join paths and refusal rules attached."""
    return (
        "You are answering a question about a retail bank's data, using the FinChat "
        "MCP tools. Stay inside this perimeter:\n\n"
        f"{json.dumps(knowledge.perimeter(), indent=2)}\n\n"
        "Canonical join paths:\n"
        f"{knowledge.join_bullets()}\n"
        "Rules you must follow:\n"
        f"{knowledge.refusal_bullets()}\n"
        "Call describe_data_model before assuming what a term means.\n\n"
        f"Question: {question}"
    )


def main() -> None:
    transport = os.getenv("FINCHAT_MCP_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        # DNS-rebinding protection trusts localhost only by default, so a server
        # behind Cloud Run's front end rejects the proxied Host with 421 unless the
        # public host is allow-listed. Nothing in the error says so.
        allowed = [h for h in os.getenv("FINCHAT_MCP_ALLOWED_HOSTS", "").split(",") if h]
        if allowed:
            from mcp.server.transport_security import TransportSecuritySettings

            mcp.settings.transport_security = TransportSecuritySettings(
                allowed_hosts=allowed,
                allowed_origins=[f"https://{h}" for h in allowed],
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
