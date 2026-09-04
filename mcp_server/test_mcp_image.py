"""Guard: every module the MCP server loads must be COPYed into its image.

The same failure mode that cost the UI and agent images an outage, with one extra
turn of the screw. This server reaches *outside its own directory* for four modules
owned by other services, and it loads them lazily — `_okf_context` on the first
knowledge call, `bq.py` only when demo mode is reached. A missing COPY therefore
survives the build, survives boot, survives the health check, and surfaces as one
broken tool in production.

So the guard asserts both directions: this package's own modules, and the
cross-service files the code names by path.
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Join backslash continuations before matching: the COPY lines wrap, and a
# line-by-line scan would silently ignore everything after the first wrap.
DOCKERFILE = re.sub(r"\\\s*\n\s*", " ",
                    (HERE / "Dockerfile").read_text(encoding="utf-8"))
COPIED = " ".join(l for l in DOCKERFILE.splitlines() if l.strip().startswith("COPY"))


def test_every_module_in_this_package_is_copied():
    missing = [p.name for p in sorted(HERE.glob("*.py"))
               if not p.name.startswith("test_") and p.name not in COPIED]
    assert not missing, f"not COPYed into the MCP image: {missing}"


def test_every_repo_path_the_code_reaches_for_is_copied():
    """Every `REPO_ROOT / ...` expression in the source must ship.

    Derived from the source rather than listed here, so a fifth borrowed module —
    or a data file like the KB corpus, which is read directly rather than imported —
    cannot pass by leaving this test alone.
    """
    sources = "\n".join(p.read_text(encoding="utf-8") for p in HERE.glob("*.py")
                        if not p.name.startswith("test_"))
    # REPO_ROOT / "a" / "b" / "c.py"  ->  a/b/c.py
    wanted = {"/".join(re.findall(r'"([^"]+)"', expr))
              for expr in re.findall(r'REPO_ROOT\s*/\s*((?:"[^"]+"\s*/\s*)*"[^"]+")', sources)}
    assert wanted, "no repo paths found — did the module change shape?"

    for path in sorted(wanted):
        target = HERE.parent / path
        assert target.exists(), f"{path} does not exist in the repo"
        # Docker COPYs a directory by its path and a file by its name.
        needle = path if target.is_dir() else Path(path).name
        assert needle in COPIED, f"{path} is read at runtime but not COPYed"


def test_published_data_files_ship_too():
    """The resources are half the surface, and none of them is a .py file.

    `finchat://contracts/{name}` over an empty directory answers "no contract",
    which reads as a governance gap rather than as a packaging bug.
    """
    assert "contracts/" in COPIED
    assert "ontology.yaml" in COPIED
