"""Guard: a backend outage must reach the user, not be dressed up as demo data.

Companion to test_block_surfacing.py, which covers the other way the SPA used to lie:
a REFUSAL shown as an outage. This is an OUTAGE shown as an answer. On 2026-09-19 the
agent answered every chat with a 500 (a ModuleNotFoundError in its tool path) for hours,
the api() helper turned that into {__demo:true}, and agentAnswer() served a balance from
client-side grounding as if nothing had happened. Nobody reported it because nothing
looked wrong.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """Source of one top-level `function name(` up to the next top-level function."""
    start = SRC.index(f"function {name}(")
    nxt = SRC.find("\nfunction ", start + 1)
    nxt2 = SRC.find("\nasync function ", start + 1)
    ends = [e for e in (nxt, nxt2) if e != -1]
    return SRC[start:min(ends)] if ends else SRC[start:]


def test_5xx_from_a_live_backend_is_an_outage_not_demo():
    api = _fn("api")
    assert "if (r.status >= 500 && r.status !== 502) return outage(" in api


def test_a_json_502_is_left_for_the_block_branch():
    """server.py answers 502 with a JSON body when Model Armor withholds a response. If
    api() called that an outage, the refusal would never reach agentAnswer's block branch
    and test_block_surfacing's contract would be dead code."""
    api = _fn("api")
    assert "r.status !== 502" in api
    non_json = api.index("catch(e) { return outage(kind, r.status, \"non-JSON body\"); }")
    assert non_json < api.index("r.status !== 502")


def test_a_non_json_body_is_an_outage_not_demo():
    """'Internal Server Error' is text; r.json() throwing must not mean 'demo mode'."""
    api = _fn("api")
    assert "catch(e) { return outage(kind, r.status, \"non-JSON body\"); }" in api


def test_503_still_means_not_configured():
    """The BFF answers 503 for a backend with no URL; that IS demo mode and stays so."""
    api = _fn("api")
    assert "if (r.status === 503) return { __demo:true };" in api


def test_a_network_failure_is_demo_only_on_a_standalone_page():
    api = _fn("api")
    assert "return S.demo ? { __demo:true } : outage(kind, 0, \"network\");" in api


def test_an_outage_is_never_silent():
    out = _fn("outage")
    assert "console.error(" in out
    assert 'el("outageBadge")' in out and 'classList.remove("hidden")' in out
    assert 'id="outageBadge"' in SRC


def test_chat_says_outage_instead_of_answering_from_demo_data():
    chat = _fn("agentAnswer")
    assert "if (res.__outage)" in chat
    assert "unavailable right now" in chat


def test_the_outage_branch_precedes_the_demo_fallback():
    """Order is the fix. outage() returns __demo:true so other pages keep rendering, which
    means the chat fallback would swallow it if it ran first — exactly as before."""
    chat = _fn("agentAnswer")
    assert chat.index("res.__outage") < chat.index("res.__demo || res.error || !res.response")


def test_the_block_branch_still_precedes_the_outage_branch():
    """A 502 is a withheld response (a refusal), not an outage; test_block_surfacing owns
    that contract and this branch must not get in front of it."""
    chat = _fn("agentAnswer")
    assert chat.index("res.__status === 400 || res.__status === 502") < chat.index("res.__outage")
