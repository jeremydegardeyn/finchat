"""Guard: every relative link in the docs points at a file that exists.

Four broken ADR cross-references were sitting in `docs/` — `0020-oauth-dcr-proxy.md` and
`0024-ai-gateway-chokepoint.md`, neither of which has ever been a filename here. They were
written from what the ADR is *about* rather than what it is *called*, which is exactly the
mistake nothing catches: the prose reads correctly, the link is plausible, and it only
fails for a reader who clicks.

That matters more in this repository than most. The ADRs are the argument; a decision
whose justification 404s is a decision nobody can check.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

# Relative links to files we can verify. Anchors are stripped; external URLs are somebody
# else's problem and checking them would make this test need a network.
LINK = re.compile(r"\]\((?!https?:|mailto:)([^)\s#]+)")
CHECKED_SUFFIXES = {".md", ".svg", ".png", ".mmd", ".sql", ".py", ".yaml", ".yml", ".tf"}


def test_every_relative_doc_link_resolves():
    broken = []
    for doc in sorted(DOCS.rglob("*.md")):
        for match in LINK.finditer(doc.read_text(encoding="utf-8")):
            target = match.group(1)
            if Path(target).suffix.lower() not in CHECKED_SUFFIXES:
                continue
            if not (doc.parent / target).exists():
                broken.append(f"{doc.relative_to(REPO).as_posix()} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_the_guard_is_actually_looking_at_something():
    """A link checker that finds no links passes forever. Assert it sees a real corpus."""
    links = sum(len(LINK.findall(d.read_text(encoding="utf-8")))
                for d in DOCS.rglob("*.md"))
    assert links > 50, f"only {links} relative links found — has docs/ moved?"
