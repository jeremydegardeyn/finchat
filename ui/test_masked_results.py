"""Guard: a masked column must not reach the user as a zero.

An analyst asked for total deposits by segment. Every amount was masked to NULL at their
access level, and the managed Conversational Analytics agent narrated it as "all
customers have 0 overdraft events" — a policy outcome reported as a fact, in a bank.

The `masked_null` refusal rule already forbids exactly that and is compiled into the CA
system instruction. It was ignored, which is the point: a prompt rule is not a control
over a model we do not run. These tests pin the deterministic check that is.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import masked_results  # noqa: E402


def _parsed(rows, answer="All customers have 0 deposits."):
    cols = list(rows[0].keys()) if rows else []
    return {"answer": answer, "sql": "SELECT ...", "columns": cols, "rows": rows}


# --- detection ---------------------------------------------------------------

def test_a_fully_masked_measure_is_detected():
    rows = [{"segment": "RETAIL", "total_deposits": None},
            {"segment": "PREMIER", "total_deposits": None}]
    assert masked_results.empty_columns(["segment", "total_deposits"], rows) == ["total_deposits"]


def test_a_column_with_any_value_is_not_flagged():
    """One real value proves the column is readable; the zeros are then real zeros."""
    rows = [{"segment": "RETAIL", "total_deposits": None},
            {"segment": "PREMIER", "total_deposits": 1400}]
    assert masked_results.empty_columns(["segment", "total_deposits"], rows) == []


def test_zero_is_a_value_not_a_null():
    """The failure being guarded against is NULL read as zero. Actual zeros are data."""
    rows = [{"segment": "RETAIL", "overdraft_events": 0},
            {"segment": "PREMIER", "overdraft_events": 0}]
    assert masked_results.empty_columns(["segment", "overdraft_events"], rows) == []


def test_no_rows_produces_no_finding():
    """Over zero rows every column is trivially all-NULL. 'No rows' is a different
    answer and needs no note about masking."""
    assert masked_results.empty_columns(["segment"], []) == []


def test_a_column_absent_from_the_rows_is_not_called_empty():
    """Absent is not empty, and reporting it as empty would invent a finding."""
    rows = [{"segment": "RETAIL"}]
    assert masked_results.empty_columns(["segment", "total_deposits"], rows) == []


def test_every_masked_column_is_reported():
    rows = [{"segment": "RETAIL", "total_deposits": None, "lowest_balance": None}]
    assert masked_results.empty_columns(
        ["segment", "total_deposits", "lowest_balance"], rows) == ["total_deposits", "lowest_balance"]


# --- the annotation the user actually reads ----------------------------------

def test_the_note_contradicts_zero_and_comes_first():
    rows = [{"segment": "RETAIL", "total_deposits": None}]
    out = masked_results.annotate(_parsed(rows))
    assert out["masked_columns"] == ["total_deposits"]
    assert out["answer"].startswith("Note:")
    assert "not as zero" in out["answer"]
    # The agent's own narrative is kept, not replaced — it is simply no longer first.
    assert "All customers have 0 deposits." in out["answer"]


def test_the_note_names_the_column_and_the_row_count():
    rows = [{"segment": s, "total_deposits": None} for s in ("A", "B", "C", "D")]
    out = masked_results.annotate(_parsed(rows))
    assert "`total_deposits`" in out["answer"]
    assert "all 4 rows" in out["answer"]


def test_masking_is_offered_as_likely_not_asserted():
    """An all-NULL column is either masking or a genuinely empty result. The note must
    not claim to know which."""
    rows = [{"segment": "RETAIL", "total_deposits": None}]
    answer = masked_results.annotate(_parsed(rows))["answer"]
    assert "most likely" in answer
    assert "policy outcome" in answer


def test_service_account_queries_do_not_blame_masking():
    """Without a propagated end-user token, masking cannot be the explanation, and
    suggesting it would send someone to request access they already hold."""
    rows = [{"segment": "RETAIL", "total_deposits": None}]
    answer = masked_results.annotate(_parsed(rows), user_credentials=False)["answer"]
    assert "masking does not explain it" in answer
    assert "policy outcome" not in answer


def test_a_clean_result_is_returned_untouched():
    rows = [{"segment": "RETAIL", "total_deposits": 1400}]
    parsed = _parsed(rows, answer="Retail deposited 1400.")
    out = masked_results.annotate(parsed)
    assert out == parsed
    assert "masked_columns" not in out


def test_an_empty_answer_still_gets_the_note():
    """CA sometimes returns rows with no narrative. The note is the whole answer then."""
    rows = [{"segment": "RETAIL", "total_deposits": None}]
    out = masked_results.annotate(_parsed(rows, answer=""))
    assert out["answer"].startswith("Note:")


def test_annotate_does_not_mutate_its_input():
    rows = [{"segment": "RETAIL", "total_deposits": None}]
    parsed = _parsed(rows)
    before = dict(parsed)
    masked_results.annotate(parsed)
    assert parsed == before
