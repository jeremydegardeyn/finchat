"""Guard: every column the loan API filters on must be projected by the view it queries.

The failure this exists for: `GET /v1/loans?account_id=` was added for the process layer
(ADR-0030) and filtered on `account_id`. The base table `loan_request` has that column.
The `loan_status` VIEW did not project it, and the view is what the query hits — so every
deployed call returned `400 Unrecognized name: account_id`, and every test passed.

Tests passed because they run against the in-memory demo store, which is a dict: it has
no view, no projection, and nothing to disagree with. The whole class of "the code and
the schema drifted" is invisible from inside a demo mode, so it needs a check that reads
the schema, and the schema in this repo is DDL text.

The symptom was quiet, which is the other half of why it survived: the process API
degrades per source, so a customer overview came back complete-looking with `loans: []`
and `partial: ["loans"]`. Correct behaviour, and it makes a broken join look like a
customer with no loans.
"""
import ast
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DDL = HERE.parent / "schemas" / "ddl.sql"


def _view_columns(view: str) -> set[str]:
    """Column names the named view exposes.

    Deliberately literal: it reads the SELECT list of `CREATE OR REPLACE VIEW <view>`
    and takes the last identifier of each item (`r.account_id` -> `account_id`,
    `ld.decision AS final_decision` -> `final_decision`). A real SQL parser would be
    better and is not worth a dependency for one view; if this stops understanding the
    DDL it fails loudly rather than passing vacuously — see the sanity assert below.
    """
    sql = DDL.read_text(encoding="utf-8")
    start = sql.index(f"VIEW `${{PROJECT}}.finchat_loans_${{ENV}}.{view}`")
    body = sql[start:sql.index(";", start)]
    # The view's own SELECT list is the last one before the first FROM at depth 0 — here,
    # the SELECT that follows the CTE block.
    select = body.rindex("\nSELECT\n")
    projection = body[select + len("\nSELECT\n"):body.index("\nFROM ", select)]
    projection = re.sub(r"--[^\n]*", "", projection)  # comments are not columns
    columns = set()
    for item in projection.split(","):
        item = item.strip()
        if not item:
            continue
        name = re.split(r"\s+AS\s+", item, flags=re.I)[-1].strip()
        columns.add(name.rsplit(".", 1)[-1])
    return columns


def _filtered_columns(module: Path, view: str) -> set[str]:
    """Columns compared to a query parameter in any function that queries `view`.

    Reads the AST rather than grepping the file: `store.py` names `loan_status` in
    prose too, and a guard that fires on the comment explaining the guard is one people
    fix by deleting the explanation.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        strings = [n.value for n in ast.walk(fn)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        joined = " ".join(strings)
        if view not in joined:
            continue
        for s in strings:
            m = re.fullmatch(r"(\w+)\s*=\s*@\w+", s.strip())
            if m:
                out.add(m.group(1))
    return out


def test_the_view_projects_every_column_the_api_filters_on():
    columns = _view_columns("loan_status")
    # If the DDL is reshaped past what the reader above understands, this is the line
    # that says so — rather than an empty set quietly satisfying every assertion below.
    assert {"loan_id", "status", "submitted_at"} <= columns, (
        f"could not read the loan_status projection (got {sorted(columns)})")

    filtered = _filtered_columns(HERE / "store.py", "loan_status")
    assert filtered, "no filtered columns found in store.py — has the query shape changed?"
    missing = sorted(filtered - columns)
    assert not missing, (
        f"{missing} filtered in store.py but not projected by the loan_status view. "
        "BigQuery answers 400 Unrecognized name; demo mode is a dict and cannot.")
