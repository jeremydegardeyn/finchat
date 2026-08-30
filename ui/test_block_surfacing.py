"""Guard: a safety block must reach the user, not be swallowed as an outage (ADR-0026).

The SPA distinguishes two failures that look identical in JSON: a backend being down
(fall back to client-side grounding) and a request the control REFUSED (say so). Before
this guard existed it conflated them, so a prompt carrying an SSN was screened out by
Model Armor, raised a ServiceNow incident, and was then answered anyway from the
client-side path with no indication anything had been blocked. The control worked; only
the screen disagreed.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")


def test_api_helper_preserves_the_http_status_on_failure():
    """Without the status, a 400 refusal is just an object with .error set."""
    assert "j.__status = r.status" in SRC


def test_a_block_is_surfaced_rather_than_falling_through_to_demo_data():
    assert "res.__status === 400 || res.__status === 502" in SRC


def test_the_block_branch_precedes_the_demo_fallback():
    """Order is the whole fix: the refusal check must run BEFORE the .error catch-all,
    or the fallback swallows it exactly as it did before."""
    block = SRC.index("res.__status === 400")
    fallback = SRC.index("res.__demo || res.error || !res.response")
    assert block < fallback


def test_both_screening_directions_are_covered():
    """server.py returns 400 for a blocked prompt and 502 for a withheld response."""
    server = (Path(__file__).resolve().parent / "server.py").read_text(encoding="utf-8")
    assert "status_code=400" in server and "status_code=502" in server
