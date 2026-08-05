"""Analyst intent routing — the keyword half, with no web dependency.

Extracted from server.py so it can be tested without FastAPI installed. That is not
cosmetic: this code path is the fallback the platform runs on when the model classifier
fails, and it silently carried every analyst question for a full session because nothing
exercised it in isolation.

The model classifier lives in server.py (it needs credentials and HTTP); everything here
is pure and deterministic.
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


