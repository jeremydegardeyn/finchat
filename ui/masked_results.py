"""Say when a column came back empty because policy masked it, not because data is absent.

A masked reader gets NULL for PII_FINANCIAL columns by design (ADR-0019). The
`masked_null` refusal rule already tells every agent never to report that as missing,
empty or zero, and that rule is compiled into the Conversational Analytics system
instruction. The managed Data Agent ignored it anyway and answered "all customers have 0
overdraft events" over a fully masked column.

That is the distinction refusal-escalation.md draws about itself: those rules shape
behaviour, they are not the control. A prompt cannot bind a model we do not run, so this
module checks the rows that actually came back instead of trusting the narrative written
over them. It asserts nothing about WHY a column is empty — an all-NULL column is either
masking or a genuinely empty result, and the note says which is likely without claiming
to know.
"""
from __future__ import annotations


def empty_columns(columns: list, rows: list) -> list:
    """Columns present in the result whose value is NULL in every row.

    A column missing from the rows entirely is not "empty", it is absent, and saying
    otherwise would invent a finding. Requires at least one row: over zero rows every
    column is trivially all-NULL, and "no rows" is a different answer that needs no note.
    """
    if not rows:
        return []
    out = []
    for col in columns or []:
        present = [r for r in rows if isinstance(r, dict) and col in r]
        if present and all(r.get(col) is None for r in present):
            out.append(col)
    return out


def _note(empties: list, row_count: int, user_credentials: bool) -> str:
    names = ", ".join(f"`{c}`" for c in empties)
    subject = "column" if len(empties) == 1 else "columns"
    verb = "was" if len(empties) == 1 else "were"

    if user_credentials:
        why = ("At a masked access level, PII_FINANCIAL values such as amounts and "
               "balances are returned as NULL by data policy, so this is most likely a "
               "policy outcome rather than an absence of data.")
    else:
        # No end-user token was propagated, so masking is not the explanation and saying
        # it might be would send someone to request access they already have.
        why = ("This query did not run under end-user credentials, so masking does not "
               "explain it; the underlying rows appear to hold no values.")

    return (f"Note: the {subject} {names} {verb} NULL in all {row_count} rows. {why} "
            f"Read the result below as 'not available', not as zero.")


def annotate(parsed: dict, *, user_credentials: bool = True) -> dict:
    """Return `parsed` with an all-NULL column flagged in the answer the user reads.

    The note is PREPENDED rather than replacing the answer. The narrative comes from the
    managed agent and may still be wrong, but the correction is then the first thing read
    rather than a caveat under a confident sentence.
    """
    empties = empty_columns(parsed.get("columns") or [], parsed.get("rows") or [])
    if not empties:
        return parsed

    out = dict(parsed)
    out["masked_columns"] = empties
    note = _note(empties, len(parsed.get("rows") or []), user_credentials)
    answer = (parsed.get("answer") or "").strip()
    out["answer"] = f"{note}\n\n{answer}" if answer else note
    return out
