"""Analyst intent routing — the decision, with no transport and no credentials.

Which capability should answer an analyst's question is a business rule, and every
channel offering an analyst surface needs the same answer, so it belongs to the process
layer (ADR-0030) rather than to whichever channel asked first.

**This file exists twice, byte-identical, and CI enforces that**
(`scripts/test_routing_copies.py`):

    products/process/api/analyst_routing.py   ← the owner
    ui/intent.py                              ← the BFF's copy

A single file would be better and is not reachable: every service image is built with
its own directory as the Docker context, so `ui/` cannot COPY out of `products/`. The
alternative was moving the UI to a repo-root build context, which changes how the live
front end deploys — a worse trade than a copy that cannot drift. Same pattern, and same
reasoning, as the two copies of `gateway_llm.py`. Edit the owner and sync the copy; the
test tells you when you have not.

What is here: the keyword tables, the model prompt, the precedence rules, and the order
classifiers are tried in. What is deliberately NOT here: the model calls themselves.
Callers inject those, which keeps this module free of HTTP, of the gateway client, and
of the end-user credential propagation that keeps the analyst *handlers* in the BFF
(ADR-0019). A rule you can test without a credential is a rule that gets tested.

Two failures are encoded in the shape of this file, both already paid for:

  * The heuristic must stand on its own, because the day it is reached is the day the
    model path is already broken. It silently carried every analyst question for a full
    session while nothing exercised it in isolation.
  * The precedence rules existed in two copies — one for the gateway path, one for the
    direct-Vertex fallback — so a fix to one missed the other. `parse_intent` is now the
    only place a model's answer is read.
"""
from __future__ import annotations

import re

PLATFORM_WORDS = ("adr", "architecture", "how does finchat", "how is finchat", "why did we",
                   "why was", "runbook", "deploy", "terraform", "module", "repo",
                   "gateway", "registry", "pipeline", "ci ", "cicd", "eval harness",
                   "increment", "reference implementation", "decision record", "codebase",
                   "implemented", "supported", "what does finchat",
                   # Added after the router silently fell back for a full session: these
                   # only appear when someone is asking about the SYSTEM, never about the
                   # bank's data. The heuristic has to stand on its own, because the day
                   # it is reached is the day the model path is already failing.
                   "for finchat", "in finchat", "of finchat", "finchat's",
                   "auth pattern", "authentication", "sign-in", "sign in", "oauth",
                   "token budget", "budget", "rate limit", "quota", "service account",
                   "identity", "persona", "permission", "iam", "scope",
                   "agent", "canary", "drift", "pinning", "model version",
                   "bigtable", "spanner", "firestore", "cloud run", "bigquery omni",
                   "schema of", "how do you", "how do we", "does finchat", "can finchat")

KB_WORDS = ("fee", "polic", "hour", "branch", "atm", " open", "close", "term", "condition",
             "privacy", "eligib", "require", "interest", "rate", "offer", "document", "contact",
             "support", "location", "how do i", "what is a", "limit", "disclosure")
AN_WORDS = ("how many", "count", "number of", "total", "sum", "average", "avg", "median", "top ",
             " most ", "least", "list ", "per segment", "by segment", "per customer", "distribution",
             "breakdown", "customers with", "which customer", "trend", "over time", "compare",
             "percentage", "ratio", "largest", "smallest", "highest", "lowest", "how much")
SEM_WORDS = ("what does", "what is a ", "definition", "defined", "define", "mean", "how is ",
              "calculated", "computed", "what columns", "what fields", "schema", "join",
              "related to", "what's in", "what is in", "contain", "which view", "which table",
              "data model", "column mean")


def hits(ql: str, words) -> int:
    """Count keyword matches on WORD BOUNDARIES, not raw substrings.

    Naive `w in ql` matched "count" inside "dim_account", so any question mentioning an
    account scored as analytics — including "how do fact_transaction and dim_account
    join", which is plainly a semantics question.

    The boundary is enforced at the START of the keyword only, never the end. Several
    entries are deliberate stems — "fee" for fees, "polic" for policy/policies, "eligib"
    for eligible/eligibility — so a trailing boundary would break them and lose more than
    it fixed. Leading-only kills the false positive ("count" preceded by "ac" fails) while
    keeping stems working.
    """
    n = 0
    for w in words:
        if re.search(r"(?<![a-z0-9_])" + re.escape(w.strip()), ql):
            n += 1
    return n


def heuristic_intent(q: str) -> str:
    ql = q.lower()
    scores = {
        "kb": hits(ql, KB_WORDS),
        "analytics": hits(ql, AN_WORDS),
        "semantics": hits(ql, SEM_WORDS),
        # Weighted x2: platform terms are specific ("adr", "terraform", "runbook") where
        # KB/analytics terms are common words, so an unweighted tie goes the wrong way.
        "platform": 2 * hits(ql, PLATFORM_WORDS),
    }
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    # Nothing matched. Defaulting to analytics is a real choice with a real cost: it is
    # why "what is the auth pattern for finchat" came back as a data query. Kept as the
    # default because it is the most common analyst intent, but logged — a fallback that
    # fires constantly means the router above is broken, and that should be visible.
    print(f"intent heuristic: no keyword match, defaulting to analytics for {q[:80]!r}")
    return "analytics"


# --- the model classifier ----------------------------------------------------
MODES = ("analytics", "kb", "semantics", "platform")

# Written as one literal rather than concatenated fragments so the newlines are visible
# in the source. The text is unchanged from the copy that lived in server.py.
ROUTING_PROMPT = """You route a bank analyst's question to one of four tools. Reply with ONE word.
ANALYTICS = a quantitative question about the bank's DATA VALUES (counts, sums, averages, lists, per-segment/per-customer metrics over transactions, accounts, customers, loans, overdrafts).
KB = a question answerable from the bank's POLICY/PRODUCT DOCUMENTS (fees, policies, branch hours, terms, eligibility, rates offered, how-to).
SEMANTICS = a question about the DATA MODEL ITSELF — what a metric means, how it is defined/calculated, what a table or view contains, or how tables join. (Not a data value; not a policy.)
PLATFORM = a question about how the FinChat PLATFORM ITSELF is built or operated — architecture, an ADR or design decision, a service, module, pipeline, the gateway, the agent registry, CI/CD, Terraform, runbooks, or what the platform supports. (About the SYSTEM, not the bank's data or the bank's policies.)
Question: {question}
Answer (ANALYTICS, KB, SEMANTICS, or PLATFORM):"""


def routing_prompt(question: str) -> str:
    return ROUTING_PROMPT.format(question=question)


def parse_intent(text):
    """Read a classifier's one-word answer — the single copy of the precedence rules.

    PLATFORM is checked first because it is the most specific: "how is the analytics
    pipeline built" carries tokens that would otherwise score as ANALYTICS. KB only wins
    when the answer does not also mention analytics, which is how a hedged reply used to
    be read as a policy question.

    Returns None when the text names no mode, so a caller can try the next classifier
    rather than treating an empty answer as a decision. That distinction is what the two
    duplicated copies made easy to get wrong.
    """
    txt = (text or "").upper()
    if "PLATFORM" in txt:
        return "platform"
    if "SEMANTIC" in txt:
        return "semantics"
    if "KB" in txt and "ANALYTIC" not in txt:
        return "kb"
    if "ANALYTIC" in txt:
        return "analytics"
    return None


def classify(question: str, classifiers) -> str:
    """Try each classifier on the rendered prompt in order; fall back to keywords.

    Each classifier takes the prompt and returns the model's text, or None. One that
    raises is treated as unavailable rather than fatal: the caller's ordering already
    expresses preference (governed gateway first, direct model second), and a routing
    failure must degrade to the heuristic instead of failing the question.
    """
    prompt = routing_prompt(question)
    for call in classifiers:
        try:
            mode = parse_intent(call(prompt))
        except Exception:
            continue
        if mode:
            return mode
    return heuristic_intent(question)


async def classify_async(question: str, classifiers) -> str:
    """`classify` for callers whose classifiers are coroutines.

    The BFF's two paths are not the same shape — the governed gateway call is
    synchronous and the direct-Vertex fallback is an awaited HTTP request — so a single
    sync signature could not host both without one of them blocking the loop. Same
    ordering, same fallback, same precedence; only the await differs.
    """
    prompt = routing_prompt(question)
    for call in classifiers:
        try:
            mode = parse_intent(await call(prompt))
        except Exception:
            continue
        if mode:
            return mode
    return heuristic_intent(question)
