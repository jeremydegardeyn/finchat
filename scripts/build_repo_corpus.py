#!/usr/bin/env python3
"""
Build the PLATFORM knowledge corpus from the repo's own documentation.

Why a second corpus rather than more rows in the first
-----------------------------------------------------
`products/transactions/agent/kb/corpus.jsonl` is what a *customer* is answered from —
fees, branch hours, terms. The Banking Assistant's instruction says to ground answers
only in the snippets that come back, so putting ADRs in the same store means a customer
asking about overdraft fees can be answered with agent-registry internals. That is a
quality and persona regression, not a feature.

So repo docs land in their own table (`platform_chunks`), searched by their own tool, on
the analyst/admin surface only. Same dataset, same embedding model, same retrieval
pattern — different corpus, different audience.

What gets ingested
------------------
  docs/*.md            architecture deliverables
  docs/adr/*.md        decision records
  knowledge/**/*.md    the OKF governance bundle
  README.md            executive overview
  eval/README.md       AgentOps harness
  products/*/README.md per-product notes

Chunking is by markdown heading rather than fixed token windows: an ADR section is
already a semantically complete unit, and splitting mid-argument is how RAG starts
citing half a decision. Sections longer than MAX_CHARS are split on paragraph
boundaries with the heading trail repeated, so a chunk always knows what it belongs to.

Usage:
    python scripts/build_repo_corpus.py                 # writes the JSONL
    python scripts/build_repo_corpus.py --stats         # summarise, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "products" / "transactions" / "agent" / "kb" / "repo_corpus.jsonl"

MAX_CHARS = 4000   # generous: these are prose sections, not code
MIN_CHARS = 120    # below this a chunk is a stub heading and adds only noise

# (glob, category). Category drives the citation label an analyst sees, so it is the
# document's *kind*, not its directory.
SOURCES = [
    ("docs/adr/*.md", "decision-record"),
    ("docs/*.md", "architecture"),
    ("knowledge/**/*.md", "governance"),
    ("README.md", "overview"),
    ("eval/README.md", "evaluation"),
    ("products/*/README.md", "product"),
    ("products/*/*/README.md", "product"),
    ("infra/modules/*/README.md", "infrastructure"),
]

SKIP = {"docs/adr/README.md"}  # pure index, no content of its own


def _clean(md: str) -> str:
    """Strip fenced code and tables. Both retrieve badly and neither answers the kind of
    question this corpus is for — 'how does X work and why', not 'show me the SQL'."""
    md = re.sub(r"```.*?```", "", md, flags=re.S)
    md = "\n".join(l for l in md.splitlines() if not re.match(r"^\s*\|", l))
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def _split_long(text: str, limit: int) -> list[str]:
    """Split on blank lines, never mid-paragraph."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > limit:
            out.append(cur.strip())
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        out.append(cur.strip())
    return out


def chunk_markdown(path: Path, category: str) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Drop YAML front matter (the knowledge/ bundle uses it).
    raw = re.sub(r"\A---\n.*?\n---\n", "", raw, flags=re.S)
    body = _clean(raw)
    rel = path.relative_to(REPO).as_posix()

    # Document title: first H1, else the filename.
    m = re.search(r"^#\s+(.+)$", body, flags=re.M)
    doc_title = m.group(1).strip() if m else path.stem.replace("-", " ").title()

    # Split on H2/H3; keep the heading with its body.
    parts = re.split(r"^(#{2,3})\s+(.+)$", body, flags=re.M)
    chunks: list[dict] = []
    preamble = parts[0].strip()
    if len(preamble) >= MIN_CHARS:
        chunks.append({"heading": None, "text": preamble})
    for i in range(1, len(parts), 3):
        heading, text = parts[i + 1].strip(), parts[i + 2].strip()
        if len(text) < MIN_CHARS:
            continue
        for piece in _split_long(text, MAX_CHARS):
            chunks.append({"heading": heading, "text": piece})

    rows = []
    for n, c in enumerate(chunks):
        title = f"{doc_title} — {c['heading']}" if c["heading"] else doc_title
        # The source path is prepended to the content, not just stored alongside it, so
        # the model can cite where an answer came from without a second lookup.
        content = f"[{rel}] {title}\n\n{c['text']}"
        rows.append({
            "doc_id": f"{rel}#{n}",
            "title": title[:300],
            "category": category,
            "source_path": rel,
            "content": content,
        })
    return rows


def build() -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    for pattern, category in SOURCES:
        for path in sorted(REPO.glob(pattern)):
            rel = path.relative_to(REPO).as_posix()
            if rel in SKIP or rel in seen or not path.is_file():
                continue
            seen.add(rel)
            rows.extend(chunk_markdown(path, category))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the platform docs corpus.")
    ap.add_argument("--stats", action="store_true", help="summarise without writing")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    rows = build()
    import collections
    by_cat = collections.Counter(r["category"] for r in rows)
    files = len({r["source_path"] for r in rows})
    chars = sum(len(r["content"]) for r in rows)

    print(f"{len(rows)} chunks from {files} files ({chars:,} chars)")
    for cat, n in by_cat.most_common():
        print(f"  {cat:<16} {n:>4}")

    if args.stats:
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
