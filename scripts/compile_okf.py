#!/usr/bin/env python3
"""Compile machine-readable facts from the OKF knowledge bundle into a committed
Python module (ui/_okf_context.py) consumed by the analyst grounding in ui/server.py.

The OKF bundle is the single source of truth. The structured `perimeter` and `joins`
frontmatter on the analyst playbooks generate BOTH the Conversational Analytics
semantic perimeter (the table allow-list) AND the join-model bullets injected into
the CA system instruction — so the prose prompt, the table list, and the human-readable
playbook can no longer drift apart.

The UI Cloud Run image copies only a handful of named files, so we compile to a plain
Python module at authoring time (committed) rather than parsing the bundle at runtime:
no new runtime dependency, no bundle shipped in the image.

    Run from the repo root:   python scripts/compile_okf.py
    Drift guard (CI):         ui/test_okf_context.py
"""
from __future__ import annotations

import pathlib
import pprint

import yaml  # build-time only; the generated module has no third-party imports.

import compile_ontology  # sibling module in scripts/ (perimeter + join model SSOT)

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "knowledge"
OUT = ROOT / "ui" / "_okf_context.py"

# Descriptive concept docs (no runtime frontmatter to extract — plain semantics) that
# ground the analyst KB/semantics route. NOT playbooks (those carry behavioural SSOT)
# and NOT index.md (a manifest). Order is layer-by-layer for readable grounding.
# Inc 22 adds glossary (business terms) + reference (code sets).
CONCEPT_DIRS = ("datasets", "tables", "views", "metrics", "graph", "glossary", "reference")
# Root-level bundle docs that are also agent-relevant semantics.
ROOT_DOCS = ("limitations.md", "lineage.md")
# Inc 22: the refusal/escalation playbook is the behavioural SSOT for agent safety.
REFUSALS_MD = BUNDLE / "playbooks" / "refusal-escalation.md"
GLOSSARY_DIR = BUNDLE / "glossary"


def _frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, fm, _body = text.split("---", 2)
    return yaml.safe_load(fm) or {}


def _doc_body(path: pathlib.Path) -> str:
    """The prose of a concept doc, minus any leading YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, _fm, body = text.split("---", 2)
        return body.strip()
    return text.strip()


def _concept_corpus() -> str:
    """Concatenate the descriptive concept docs into one grounding blob. This is the
    large, static, text-only half of the bundle — meant to be prompt-cached at runtime,
    not turned into structured config like the perimeter."""
    parts = []
    for d in CONCEPT_DIRS:
        sub = BUNDLE / d
        if not sub.is_dir():
            continue
        for md in sorted(sub.glob("*.md")):
            parts.append(f"### {d}/{md.name}\n{_doc_body(md)}")
    for name in ROOT_DOCS:
        doc = BUNDLE / name
        if doc.is_file():
            parts.append(f"### {name}\n{_doc_body(doc)}")
    return "\n\n".join(parts)


def _glossary() -> list[dict]:
    """Business terms + synonyms: the vocabulary users speak, mapped to the model.
    Drives disambiguation on the semantics route and (next) retrieval query expansion."""
    out = []
    if not GLOSSARY_DIR.is_dir():
        return out
    for md in sorted(GLOSSARY_DIR.glob("*.md")):
        fm = _frontmatter(md)
        if not fm.get("term"):
            continue
        out.append({
            "term": fm["term"],
            "synonyms": list(fm.get("synonyms") or []),
            "status": fm.get("status", "proposed"),
            "maps_to": list(fm.get("maps_to") or []),
            "modelled": bool(fm.get("modelled", True)),
            "owner": fm.get("owner", ""),
        })
    return out


def _refusals() -> dict:
    """The agent-safety rules, injected into system instructions rather than retrieved."""
    fm = _frontmatter(REFUSALS_MD)
    return {
        "rules": [
            {"id": r["id"], "rule": r["rule"], "say": r["say"]}
            for r in (fm.get("refusals") or [])
        ],
        "escalate": list(fm.get("escalate_to_human") or []),
    }


def _refusal_bullets(refusals: dict) -> str:
    """Prompt-ready rendering of the refusal rules."""
    return "".join(f"- {r['rule']} If asked, say: \"{r['say']}\"\n" for r in refusals["rules"])


def build() -> dict:
    """Project the ontology SSOT + bundle docs into the values rendered into
    _okf_context.py. Perimeter and the join model come from knowledge/ontology.yaml
    (via compile_ontology); the knowledge corpus, glossary and refusal rules from the
    bundle's concept docs, glossary/ and playbooks/refusal-escalation.md."""
    model = compile_ontology.load()
    refusals = _refusals()
    return {
        "perimeter": compile_ontology.perimeter(model),
        "join_bullets": compile_ontology.join_bullets(model),
        "concept_corpus": _concept_corpus(),
        "glossary": _glossary(),
        "refusals": refusals,
        "refusal_bullets": _refusal_bullets(refusals),
        "stewardship": compile_ontology.stewardship(model),
    }


def _py(obj) -> str:
    """Render as a PYTHON literal (not JSON — `true`/`null` would not import)."""
    return pprint.pformat(obj, indent=4, width=96, sort_dicts=False)


def render(data: dict) -> str:
    return (
        '"""GENERATED by scripts/compile_okf.py from the OKF bundle in knowledge/.\n'
        "DO NOT EDIT BY HAND — run `python scripts/compile_okf.py` to regenerate.\n"
        "SSOT: knowledge/ontology.yaml (perimeter/joins/stewardship), the concept docs\n"
        "(ANALYST_KNOWLEDGE), knowledge/glossary/ (ANALYST_GLOSSARY) and\n"
        "knowledge/playbooks/refusal-escalation.md (ANALYST_REFUSALS).\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        f"ANALYST_PERIMETER = {_py(data['perimeter'])}\n\n"
        f"ANALYST_JOIN_BULLETS = {data['join_bullets']!r}\n\n"
        f"ANALYST_KNOWLEDGE = {data['concept_corpus']!r}\n\n"
        f"ANALYST_GLOSSARY = {_py(data['glossary'])}\n\n"
        f"ANALYST_REFUSALS = {_py(data['refusals'])}\n\n"
        f"ANALYST_REFUSAL_BULLETS = {data['refusal_bullets']!r}\n\n"
        f"CONCEPT_STEWARDSHIP = {_py(data['stewardship'])}\n"
    )


def main() -> None:
    # One command regenerates every ontology projection: the graph join view first,
    # then the OKF grounding module.
    compile_ontology.main()
    OUT.write_text(render(build()), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
