"""
Drift guard: the tracing module's build wiring (ADR-0035).

`observability/tracing.py` is one file that six services import, and five of them build
from their own directory and therefore cannot COPY a file above it. The build stages the
canonical copy into each context; the Dockerfiles COPY it by name. Those are two lists in
two files, and a service added to one and not the other fails in a specific, expensive
way: a `docker build` that stops on a missing COPY source, or worse, an image that is
missing a module which only `import`s inside a try/except — the exact failure the UI image
guard exists for.

Same shape as test_controls_workflow.py: hold the two lists against each other so the
disagreement is a red test rather than a red build.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "build-deploy.yml"
# CI builds the same images without pushing them, from the same service-directory
# contexts, and so needs the same staging. Omitted at first, which failed every image
# build on the first PR while the deploy would have worked — a guard that checks one of
# two build paths is a guard that reports green for half the problem.
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CANONICAL = ROOT / "observability" / "tracing.py"

# The MCP server is deliberately absent: its image builds from the repo root (ADR-0028),
# so it copies observability/tracing.py directly and needs no staging.
STAGED = (
    "ui",
    "products/transactions/agent",
    "products/transactions/api",
    "products/loans/api",
    "products/process/api",
)


def test_the_canonical_module_exists_exactly_once():
    """A committed per-service copy would defeat the point: this module is a content
    control, and a control with six copies has six versions."""
    assert CANONICAL.is_file()
    copies = [p for p in ROOT.rglob("tracing.py")
              if p != CANONICAL and ".git" not in p.parts]
    assert not copies, (
        "tracing.py exists outside observability/ — these are build artifacts and must "
        f"be gitignored, not committed: {[str(p.relative_to(ROOT)) for p in copies]}")


def _staging_block() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "observability/tracing.py" in text, "the build no longer stages tracing.py"
    return text


def test_every_staged_context_is_staged_by_the_build():
    text = _staging_block()
    # The staging loop lists contexts; each must appear in it.
    for ctx in STAGED:
        assert ctx in text, f"{ctx} is not staged by build-deploy.yml"


def test_ci_stages_it_too():
    """Both build paths, not just the one that ships. The CI matrix passes the service
    directory as the build context exactly as the deploy does, so a Dockerfile COPYing
    tracing.py fails there unless CI stages it as well."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "observability/tracing.py" in text, (
        "ci.yml does not stage tracing.py — every docker-build matrix job whose "
        "Dockerfile COPYs it will fail on the COPY")


def test_every_staged_context_copies_it_into_its_image():
    for ctx in STAGED:
        dockerfile = ROOT / ctx / "Dockerfile"
        copied = " ".join(l for l in dockerfile.read_text(encoding="utf-8").splitlines()
                          if l.strip().startswith("COPY"))
        assert "tracing.py" in copied, (
            f"{ctx}/Dockerfile stages tracing.py but never COPYs it — the module would "
            "be absent at runtime and tracing would silently never start")


def test_the_mcp_image_copies_the_canonical_path_directly():
    """The one service that builds from the repo root, and so must NOT rely on staging."""
    dockerfile = (ROOT / "mcp_server" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY observability/tracing.py" in dockerfile


def test_every_service_that_copies_it_is_in_this_list():
    """The other direction: a Dockerfile that COPYs tracing.py without being staged is a
    build that fails on the COPY, which is a red CI run someone has to bisect."""
    for dockerfile in ROOT.rglob("Dockerfile"):
        if ".git" in dockerfile.parts:
            continue
        text = dockerfile.read_text(encoding="utf-8")
        if "tracing.py" not in text:
            continue
        ctx = dockerfile.parent.relative_to(ROOT).as_posix()
        assert ctx in STAGED or ctx == "mcp_server", (
            f"{ctx}/Dockerfile COPYs tracing.py but is neither staged by the build nor "
            "the repo-root MCP build")


def test_the_pinned_otel_version_is_the_same_everywhere():
    """google-adk 2.2.0 caps opentelemetry-sdk at 1.41.1. A service that pins above the
    cap resolves fine on its own and makes the AGENT image the one that breaks, which is
    the most confusing possible place for the error to appear."""
    pins = {}
    for req in ROOT.rglob("requirements.txt"):
        if ".git" in req.parts:
            continue
        for line in req.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(opentelemetry-[a-z-]+)==([\d.]+)", line.strip())
            if m:
                pins.setdefault(m.group(1), set()).add(m.group(2))
    assert pins, "no OpenTelemetry pins found — did the requirements change?"
    for pkg, versions in pins.items():
        assert len(versions) == 1, f"{pkg} is pinned to {sorted(versions)} across services"
    # And the cap itself, stated so an upgrade has to confront it.
    assert pins.get("opentelemetry-sdk") == {"1.41.1"}, (
        "opentelemetry-sdk moved. google-adk 2.2.0 requires <=1.41.1 — check the ADK pin "
        "before raising this, and raise it everywhere at once.")
