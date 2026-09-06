"""Guard: every runtime module must be COPYed into the UI image.

The Dockerfile lists modules by name, so a new file that is not listed is simply absent
at runtime. That failure is invisible locally and, because `import` sits inside a
try/except in the request path, it surfaces as an opaque 502 rather than a crash on boot.

gateway_client.py and user_tokens.py were both missed this way. The agent image has had
this guard since retrieval.py caused the same outage; the UI image did not.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Files that are deliberately absent from the image. The exemption is narrow on
# purpose: the default has to stay "everything ships", because the failure mode of a
# missing module is an opaque 502 in one request path rather than a crash on boot.
NOT_IN_IMAGE = {
    "run_local.py",   # developer entry point; it shells out to gcloud, which the image
                      # has no reason to contain and no credentials to use
}

# Test infrastructure, excluded by convention rather than by name. `conftest.py` was in
# NOT_IN_IMAGE and the file did not exist — a dead exemption that a future ui/conftest.py
# would have inherited silently. A rule cannot go stale; a filename can.
TEST_FILES = ("test_", "conftest")


def test_every_runtime_module_is_copied_into_the_image():
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    copied = " ".join(l for l in dockerfile.splitlines() if l.strip().startswith("COPY"))
    missing = [
        p.name for p in sorted(HERE.glob("*.py"))
        if not p.name.startswith(TEST_FILES) and p.name not in NOT_IN_IMAGE
        and p.name not in copied
    ]
    assert not missing, f"not COPYed into the UI image: {missing}"


def test_index_html_is_copied():
    """The SPA is the product; shipping an image without it is a silent 404."""
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    assert "index.html" in dockerfile


def test_the_image_exemptions_still_exist():
    """An exemption for a deleted file is a hole the next same-named file falls into.

    `NOT_IN_IMAGE` suppresses the guard by filename. If one of those files is renamed or
    removed and the entry stays, a future module with that name ships nothing and the
    guard says everything is fine.
    """
    stale = sorted(n for n in NOT_IN_IMAGE if not (HERE / n).exists())
    assert not stale, f"NOT_IN_IMAGE names files that no longer exist: {stale}"
