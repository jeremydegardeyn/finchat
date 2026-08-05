"""Guard: every runtime module must be COPYed into the UI image.

The Dockerfile lists modules by name, so a new file that is not listed is simply absent
at runtime. That failure is invisible locally and, because `import` sits inside a
try/except in the request path, it surfaces as an opaque 502 rather than a crash on boot.

gateway_client.py and user_tokens.py were both missed this way. The agent image has had
this guard since retrieval.py caused the same outage; the UI image did not.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOT_IN_IMAGE = {"conftest.py"}  # test-only helpers never shipped


def test_every_runtime_module_is_copied_into_the_image():
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    copied = " ".join(l for l in dockerfile.splitlines() if l.strip().startswith("COPY"))
    missing = [
        p.name for p in sorted(HERE.glob("*.py"))
        if not p.name.startswith("test_") and p.name not in NOT_IN_IMAGE
        and p.name not in copied
    ]
    assert not missing, f"not COPYed into the UI image: {missing}"


def test_index_html_is_copied():
    """The SPA is the product; shipping an image without it is a silent 404."""
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    assert "index.html" in dockerfile
