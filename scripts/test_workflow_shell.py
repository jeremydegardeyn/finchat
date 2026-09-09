"""Guard: no workflow ships a mangled shell line-continuation.

A backslash at end of line continues a shell command. If that backslash is lost and the
`n` of the newline survives, the result is a valid-looking line with a stray `n` argument
— and YAML still parses, so nothing catches it until the job runs:

    pip install mcp==1.27.2 google-auth==2.34.0 \n            requests==2.32.3
    ERROR: Could not find a version that satisfies the requirement n

That shipped. It has happened repeatedly in this repository because the tooling that
generates these files eats one level of backslash escaping, and every safeguard that
depends on remembering has failed. A test does not have to remember.

`yaml.safe_load` passing is not evidence of anything here: the damage is inside a scalar
block, which YAML is happy to carry.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
BACKSLASH = chr(92)

# A lone `n` where a continuation should be: `\` was consumed, `n` survived.
MANGLED = re.compile(re.escape(BACKSLASH) + r"n(?=\s|$)")


def _command_lines(path: Path):
    """Lines inside a `run:` block, which is where a continuation can be broken."""
    in_run = False
    indent = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if re.match(r"-?\s*run:\s*\|", stripped) or stripped.endswith("run: |"):
            in_run, indent = True, len(line) - len(line.lstrip())
            continue
        if in_run:
            if stripped and (len(line) - len(line.lstrip())) <= indent:
                in_run = False
                continue
            if stripped and not stripped.startswith("#"):
                yield number, line


def test_no_workflow_has_a_broken_line_continuation():
    broken = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in _command_lines(workflow):
            if MANGLED.search(line):
                broken.append(f"{workflow.name}:{number}: {line.strip()[:100]}")
    assert not broken, (
        "shell line-continuations look mangled (a lost backslash leaves a stray `n` "
        "argument, and YAML still parses):\n  " + "\n  ".join(broken))


def test_the_scanner_finds_the_shape_it_is_looking_for():
    """A guard that cannot recognise the bug passes forever. Feed it the real thing."""
    sample = "          pip install mcp==1.27.2 google-auth==2.34.0 " + BACKSLASH + "n   requests==2.32.3"
    assert MANGLED.search(sample), "the scanner no longer detects the shape that shipped"
    assert not MANGLED.search("          pip install mcp==1.27.2 " + BACKSLASH)


def test_the_scanner_reads_a_real_corpus():
    """It must be looking at command lines, not an empty set."""
    total = sum(1 for w in WORKFLOWS.glob("*.yml") for _ in _command_lines(w))
    assert total > 50, f"only {total} command lines found — has the layout changed?"
