r"""Guard: no documented command has a collapsed shell line-continuation.

The sibling guard `test_workflow_shell.py` catches one shape of this bug — a lost
backslash that leaves a stray `n` argument. This catches the *other* shape, which bit
twice while writing `docs/29-mcp-from-aws.md`:

    gcloud iam workload-identity-pools create-cred-config \
      projects/.../providers/... \
      --aws

becomes, when a backslash is eaten and the interpreter joins the lines:

    gcloud iam workload-identity-pools create-cred-config   projects/...   --aws

which is still a runnable command, still renders as a code block, and is still wrong to
copy — the reader gets one unreadable line, and if the joined tokens land badly, a
command that does something other than what it says. Nothing about it looks broken, which
is why a test has to look.

The signature is the leftover indentation: three or more spaces *inside* a command line,
where a continuation used to be. Aligned trailing comments produce the same shape
legitimately, so those are excluded; the whole existing corpus is clean under this rule,
which is what makes it worth enforcing.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FENCE = re.compile(r"```(?:bash|sh|shell)\n(.*?)```", re.S)
# A gap of 3+ spaces between two non-space tokens, not introducing a comment.
COLLAPSED = re.compile(r"\S {3,}(?!#)\S")
BACKSLASH = chr(92)


def _docs():
    return sorted(REPO.joinpath("docs").rglob("*.md")) + [REPO / "README.md"]


def _command_lines(path: Path):
    text = path.read_text(encoding="utf-8")
    for fence in FENCE.finditer(text):
        offset = text[: fence.start(1)].count("\n") + 1
        for number, line in enumerate(fence.group(1).splitlines(), offset):
            if line.strip() and not line.strip().startswith("#"):
                yield number, line


def test_no_documented_command_has_a_collapsed_continuation():
    broken = []
    for doc in _docs():
        for number, line in _command_lines(doc):
            if COLLAPSED.search(line):
                broken.append(
                    f"{doc.relative_to(REPO)}:{number}: {line.strip()[:100]}")
    assert not broken, (
        "shell continuations look collapsed — a lost `" + BACKSLASH + "` joined the "
        "lines, leaving the old indentation as an interior run of spaces:\n  "
        + "\n  ".join(broken))


def test_the_scanner_finds_the_shape_that_shipped():
    """A guard that cannot recognise the bug passes forever."""
    collapsed = ("python scripts/mcp_client_example.py   --url https://x/mcp   "
                 "--service-account a@b.iam.gserviceaccount.com")
    assert COLLAPSED.search(collapsed)
    intact = "python scripts/mcp_client_example.py " + BACKSLASH
    assert not COLLAPSED.search(intact)
    assert not COLLAPSED.search("terraform apply        # requires enable_catalog"), (
        "an aligned trailing comment is not this bug and must not be reported")


def test_the_scanner_reads_a_real_corpus():
    """It must be looking at command lines, not an empty set."""
    total = sum(1 for doc in _docs() for _ in _command_lines(doc))
    assert total > 60, f"only {total} command lines found — has the layout changed?"
