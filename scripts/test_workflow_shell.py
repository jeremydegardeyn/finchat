r"""Guard: no workflow ships a mangled shell line-continuation.

A backslash at end of line continues a shell command. It gets damaged two ways, and both
have shipped from this repository.

**Shape one — the backslash is lost and the `n` of the newline survives**, leaving a
valid-looking line with a stray `n` argument. YAML still parses, so nothing catches it
until the job runs:

    pip install mcp==1.27.2 google-auth==2.34.0 \n            requests==2.32.3
    ERROR: Could not find a version that satisfies the requirement n

**Shape two — the backslash AND the newline are lost**, and the interpreter joins the
lines. The old indentation survives as a long run of spaces inside the command:

    python -m pyflakes ui/*.py scripts/*.py             2>/dev/null | grep ...

This one *runs*, which is why six of them sat in `build-deploy.yml`, `ci.yml` and
`verify-live.yml` unnoticed until a doc rewrite produced the same shape twice in one
sitting. It is not always harmless: if the joined tokens land badly the command means
something other than what it reads as, and nothing about it looks broken.

Both come from the same cause — tooling that eats one level of backslash escaping — and
every safeguard that depends on remembering has failed. A test does not have to remember.

`yaml.safe_load` passing is not evidence of anything here: the damage is inside a scalar
block, which YAML is happy to carry. `scripts/test_doc_shell.py` applies the second rule
to documented commands.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
BACKSLASH = chr(92)

# A lone `n` where a continuation should be: `\` was consumed, `n` survived.
MANGLED = re.compile(re.escape(BACKSLASH) + r"n(?=\s|$)")
# A gap of 3+ spaces mid-command: `\` and the newline were consumed, the indent survived.
# Aligned trailing comments make the same shape on purpose, so they are excluded.
COLLAPSED = re.compile(r"\S {3,}(?!#)\S")


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


def test_no_workflow_has_a_collapsed_line_continuation():
    """The shape that runs anyway, and therefore hides."""
    broken = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in _command_lines(workflow):
            if COLLAPSED.search(line):
                broken.append(f"{workflow.name}:{number}: {line.strip()[:100]}")
    assert not broken, (
        "shell continuations look collapsed — a lost `" + BACKSLASH + "` joined the "
        "lines and left the indentation as an interior run of spaces:\n  "
        + "\n  ".join(broken))


def test_the_scanner_finds_the_shapes_it_is_looking_for():
    """A guard that cannot recognise the bug passes forever. Feed it the real things."""
    sample = "          pip install mcp==1.27.2 google-auth==2.34.0 " + BACKSLASH + "n   requests==2.32.3"
    assert MANGLED.search(sample), "the scanner no longer detects the shape that shipped"
    assert not MANGLED.search("          pip install mcp==1.27.2 " + BACKSLASH)

    joined = "          deploy mcp mcp mcp             \"--set-env-vars=A=1\""
    assert COLLAPSED.search(joined), "the scanner no longer detects the joined shape"
    assert not COLLAPSED.search("          deploy mcp mcp mcp " + BACKSLASH)
    assert not COLLAPSED.search("          terraform apply        # enable_catalog"), (
        "an aligned trailing comment is not this bug and must not be reported")


def test_the_scanner_reads_a_real_corpus():
    """It must be looking at command lines, not an empty set."""
    total = sum(1 for w in WORKFLOWS.glob("*.yml") for _ in _command_lines(w))
    assert total > 50, f"only {total} command lines found — has the layout changed?"


# --- Dockerfiles -------------------------------------------------------------
# Added after the tenth occurrence, which landed in mcp_server/Dockerfile. A COPY with a
# mangled continuation is worse than a broken workflow: shape one (`\n` surviving as
# text) breaks the build loudly, but shape two — the lines simply joined — is still a
# VALID Dockerfile, because `COPY a b dest` is legal. It ships the wrong file set and
# fails at the first call, which is the worst place to find out.

def _dockerfiles():
    return sorted(p for p in REPO.rglob("Dockerfile*")
                  if ".git" not in p.parts and "node_modules" not in p.parts)


def test_no_dockerfile_has_a_broken_line_continuation():
    broken = []
    for path in _dockerfiles():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if MANGLED.search(line):
                broken.append(f"{path.relative_to(REPO)}:{number}: {line.strip()[:100]}")
    assert not broken, (
        "Dockerfile continuations look mangled (a lost backslash left a stray `n`):\n  "
        + "\n".join(broken))


def test_the_dockerfile_scanner_reads_a_real_corpus():
    found = _dockerfiles()
    assert len(found) >= 3, f"only {len(found)} Dockerfiles found — has the layout changed?"
