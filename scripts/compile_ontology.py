#!/usr/bin/env python3
"""Project the FinChat bank ontology (knowledge/ontology.yaml) into its artifacts.

The ontology is the single source of truth for the conceptual model. This module
turns it into the derived forms that other systems consume:

  - perimeter(model)     -> the analyst semantic perimeter (CA table allow-list)
  - join_bullets(model)  -> the join model rendered for the CA system instruction
                            (consumed by scripts/compile_okf.py -> ui/_okf_context.py)
  - kg_select_sql(model) -> the kg_relationships view body in products/graph/schemas/graph.sql

Before Inc 20 those three were hand-kept copies that drifted (the OKF join bullets
had 3 relationships; the kg_relationships view had 4). Now they all derive from this
file, and CI drift guards (scripts/test_ontology.py, ui/test_okf_context.py) fail the
build if any committed artifact stops matching a fresh projection.

    Regenerate:   python scripts/compile_ontology.py   (rewrites the graph.sql region)
    Drift guard:  pytest scripts/test_ontology.py
"""
from __future__ import annotations

import pathlib
import re

import yaml  # build-time only

ROOT = pathlib.Path(__file__).resolve().parents[1]
ONTOLOGY = ROOT / "knowledge" / "ontology.yaml"
GRAPH_SQL = ROOT / "products" / "graph" / "schemas" / "graph.sql"

# Markers delimiting the generated kg_relationships body inside graph.sql.
_BEGIN = "-- >>> generated from knowledge/ontology.yaml (scripts/compile_ontology.py) — do not edit by hand"
_END = "-- <<< end generated"


def load(path: pathlib.Path = ONTOLOGY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _view_of(model: dict, label: str, override: str | None) -> str:
    """Resolve a relationship endpoint (a class name, or an explicit derived view)."""
    if override:
        return override
    return model["classes"][label]["view"]


def _article(label: str) -> str:
    return "an" if label[:1].upper() in "AEIOU" else "a"


def _rows(model: dict) -> list[dict]:
    """Normalize relationships into rendered rows shared by every projection."""
    rows = []
    for r in model["relationships"]:
        rows.append({
            "from_view": _view_of(model, r["from"], r.get("from_view")),
            "from_key": r["from_key"],
            "to_view": _view_of(model, r["to"], r.get("to_view")),
            "to_key": r["to_key"],
            "from_label": r["from"],
            "to_label": r["to"],
            "verb": r["verb"],
        })
    return rows


def perimeter(model: dict) -> dict:
    """role -> [view, ...]: base classes in declaration order, then derived views."""
    out: dict[str, list[str]] = {}
    for c in model["classes"].values():
        out.setdefault(c["dataset"], []).append(c["view"])
    for dv in model.get("derived_views", []):
        out.setdefault(dv["dataset"], []).append(dv["view"])
    return out


def join_bullets(model: dict) -> str:
    """The join model as CA system-instruction bullets (matches the pre-Inc-20 format)."""
    return "".join(
        f"- {x['from_view']}.{x['from_key']} = {x['to_view']}.{x['to_key']} "
        f"({_article(x['from_label'])} {x['from_label']} {x['verb']} "
        f"{_article(x['to_label'])} {x['to_label']})\n"
        for x in _rows(model)
    )


def kg_select_sql(model: dict) -> str:
    """The kg_relationships view body. Row 1 carries AS aliases (which name the view's
    columns); the rest are compact — semantically identical, generator-friendly."""
    rows = _rows(model)
    first = rows[0]
    lines = [
        "SELECT * FROM UNNEST([",
        f"  STRUCT('{first['from_view']}' AS from_table, '{first['from_key']}' AS from_column, "
        f"'{first['to_view']}' AS to_table, '{first['to_key']}' AS to_column, "
        f"'{first['from_label']} {first['verb']} {first['to_label']}' AS relationship),",
    ]
    for x in rows[1:]:
        rel = f"{x['from_label']} {x['verb']} {x['to_label']}"
        lines.append(
            f"  STRUCT('{x['from_view']}', '{x['from_key']}', '{x['to_view']}', "
            f"'{x['to_key']}', '{rel}'),"
        )
    lines[-1] = lines[-1].rstrip(",")  # last row: no trailing comma
    lines.append("])")
    return "\n".join(lines)


def _graph_region(text: str) -> tuple[int, int]:
    b = text.index(_BEGIN)
    e = text.index(_END, b)
    return b, e + len(_END)


def render_graph_region(model: dict) -> str:
    return f"{_BEGIN}\n{kg_select_sql(model)}\n{_END}"


def sync_graph_sql(model: dict | None = None) -> bool:
    """Rewrite the marked kg_relationships region in graph.sql from the ontology.
    Returns True if the file changed."""
    model = model or load()
    text = GRAPH_SQL.read_text(encoding="utf-8")
    b, e = _graph_region(text)
    new = text[:b] + render_graph_region(model) + text[e:]
    if new != text:
        GRAPH_SQL.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = sync_graph_sql()
    print(f"graph.sql kg_relationships: {'updated' if changed else 'already in sync'}")


if __name__ == "__main__":
    main()
