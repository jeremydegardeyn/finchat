"""Router tests (docs/24).

These exist because the LLM router failed silently for an entire session: Gemini 2.5
Flash is a thinking model, `maxOutputTokens: 8` was consumed by reasoning tokens, the
candidate came back with no `parts`, and the KeyError was swallowed — so every analyst
question fell through to the keyword heuristic, which defaulted to "analytics".

Two lessons encoded here:
  1. the heuristic must stand on its own, because the day it is reached is the day the
     model path is already broken;
  2. a missing candidate must be a visible failure, not a KeyError behind an except.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intent  # noqa: E402

CASES = [
    ("what is my remaining token budget", "platform"),
    ("whatis the auth pattern for finchat", "platform"),
    ("how does the agent registry enforce tool permissions", "platform"),
    ("why did we choose Bigtable over Spanner", "platform"),
    ("what runbook do I follow to deploy", "platform"),
    ("which model version is pinned", "platform"),
    ("how many customers have overdrafts", "analytics"),
    ("what is the total transaction volume by segment", "analytics"),
    ("list the top 10 customers by balance", "analytics"),
    ("what are the overdraft fees", "kb"),
    ("when is the Lakewood branch open", "kb"),
    ("what does net_balance mean", "semantics"),
    ("how do fact_transaction and dim_account join", "semantics"),
]


@pytest.mark.parametrize("q,want", CASES)
def test_heuristic_routes_without_the_model(q, want):
    assert intent.heuristic_intent(q) == want


def test_platform_questions_are_not_swallowed_by_the_analytics_default():
    """The specific regression: system questions answered as data queries."""
    for q in ("what is my remaining token budget", "whatis the auth pattern for finchat"):
        assert intent.heuristic_intent(q) == "platform"


def test_classifier_asks_for_no_thinking_tokens():
    """Guard the actual root cause. A one-word classification needs no reasoning, and
    leaving thinking on with a tiny ceiling produces an empty candidate every time."""
    src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    i = src.index("async def _classify_intent")
    block = src[i:i + 4000]
    assert '"thinkingBudget": 0' in block, "classifier must disable thinking"
    assert '"maxOutputTokens": 8' not in block, "8 tokens is not enough headroom"


def test_every_intent_has_a_dispatch_branch():
    """A router that returns an intent nothing dispatches on silently answers wrong."""
    src = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    for intent in ("analytics", "kb", "platform"):
        assert f'mode == "{intent}"' in src, f"no dispatch branch for {intent}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
