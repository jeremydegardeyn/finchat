"""
Drift guard: every module a service imports is actually in its image.

These Dockerfiles COPY their sources **by name** rather than `COPY . .`, which is the right
call — it keeps tests, caches and stray files out of a production image. The cost is that
adding a module is two edits in two files, and skipping the second one produces a failure
that no test, no lint and no local run can see: the module is present everywhere except
inside the image, so everything passes and the container dies on import at deploy time.

That has now happened twice on the agent. `retrieval.py` was missed first, which is why
the Dockerfile carries a NOTE about it. `trajectory.py` was missed second, by someone who
had just read that NOTE — which is the point at which a comment has demonstrably stopped
working and the list needs a test holding it instead.

Deliberately not `COPY . .`: the fix for a missed file is to name it, not to stop naming
them.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Services whose Dockerfile enumerates its Python sources. A service that copies its whole
# directory does not need checking and is not listed.
ENUMERATED = (
    "products/transactions/agent",
)


def _copied_names(dockerfile: Path) -> set[str]:
    """The .py basenames a Dockerfile COPYs into the image."""
    names: set[str] = set()
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if not re.match(r"\s*COPY\s", line, re.I):
            continue
        for token in line.split()[1:]:
            if token.endswith(".py"):
                names.add(Path(token).name)
    return names


def _local_imports(src_dir: Path, available: set[str]) -> dict[str, set[str]]:
    """Which sibling modules each non-test module imports, by module name.

    Only siblings count: a name that matches a .py file next to it is a local import, and
    anything else is a package pip installs.
    """
    edges: dict[str, set[str]] = {}
    for path in sorted(src_dir.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        found: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        edges[path.name] = {f"{m}.py" for m in found if f"{m}.py" in available}
    return edges


def test_every_locally_imported_module_is_copied_into_the_image():
    offenders = []
    for service in ENUMERATED:
        src = ROOT / service
        dockerfile = src / "Dockerfile"
        assert dockerfile.is_file(), f"{service} has no Dockerfile"
        copied = _copied_names(dockerfile)
        # Every sibling module that exists, so an import can be recognised as local.
        available = {p.name for p in src.glob("*.py") if not p.name.startswith("test_")}
        for module, imports in _local_imports(src, available).items():
            if module not in copied:
                continue  # not in the image at all; the next test covers that
            for dep in sorted(imports - copied):
                offenders.append(f"{service}: {module} imports {dep}, which the "
                                 f"Dockerfile does not COPY")
    assert not offenders, (
        "modules imported at runtime but absent from the image — the container will die "
        "on import and nothing else will notice:\n  " + "\n  ".join(offenders))


def test_the_entrypoint_module_is_copied():
    """The CMD's module, specifically. Everything else is reachable only through it."""
    for service in ENUMERATED:
        dockerfile = ROOT / service / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        cmd = re.search(r"uvicorn\s+([A-Za-z_][A-Za-z0-9_]*):", text)
        assert cmd, f"{service}: no uvicorn entrypoint found in CMD"
        entry = f"{cmd.group(1)}.py"
        assert entry in _copied_names(dockerfile), (
            f"{service}: CMD runs {entry} but the Dockerfile does not COPY it")
