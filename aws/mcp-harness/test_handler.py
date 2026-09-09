"""Tests for the AWS Lambda MCP harness.

Nothing here needs AWS or a network. What is worth pinning is the handful of decisions
that are wrong in a way that looks right: the audience, the refusal paths, and the error
reporting — because in Lambda the return value IS the diagnosis, and a `{"ok": false}`
with a useless message costs a redeploy to learn anything.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.setenv("FINCHAT_MCP_URL", "https://finchat-dev-mcp-x.run.app/mcp")
    monkeypatch.setenv("FINCHAT_MCP_SERVICE_ACCOUNT", "aws@p.iam.gserviceaccount.com")
    spec = importlib.util.spec_from_file_location("harness", HERE / "handler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["harness"] = module
    spec.loader.exec_module(module)
    return module


# --- the audience -------------------------------------------------------------
def test_the_audience_drops_the_mcp_path(mod):
    """Cloud Run and mcp_server/auth.py check the token against the SERVICE url. A token
    audienced to the full path is refused with a bare 401 that never mentions the path,
    so this off-by-one-segment is expensive to debug and trivial to pin."""
    assert mod.audience("https://x-mcp.run.app/mcp") == "https://x-mcp.run.app"
    assert mod.audience("https://x-mcp.run.app") == "https://x-mcp.run.app"


# --- refusals -----------------------------------------------------------------
def test_missing_configuration_says_which_variables(mod, monkeypatch):
    monkeypatch.delenv("FINCHAT_MCP_URL")
    out = mod.lambda_handler({}, None)
    assert out["ok"] is False
    assert "FINCHAT_MCP_URL" in out["error"]


def test_arguments_must_be_an_object(mod, monkeypatch):
    """A list or a string here would reach call_tool and fail deep in the SDK."""
    monkeypatch.setattr(mod, "id_token", lambda *a: "never reached")
    out = mod.lambda_handler({"tool": "t", "arguments": ["a"]}, None)
    assert out["ok"] is False and "object" in out["error"]


def test_a_credential_failure_explains_what_to_check(mod, monkeypatch):
    """The most likely first failure in AWS, and the one whose native message is least
    useful — an attribute condition that does not match reads as missing credentials."""
    def boom(*_):
        raise mod.HarnessError("no federated credentials (RefreshError: x). Is "
                               "GOOGLE_APPLICATION_CREDENTIALS pointing at ...")
    monkeypatch.setattr(mod, "id_token", boom)
    out = mod.lambda_handler({}, None)
    assert out["ok"] is False
    assert "GOOGLE_APPLICATION_CREDENTIALS" in out["error"]


def test_the_default_tool_reads_no_customer_data(mod):
    """CloudWatch logs outlive the invocation. The default must be safe to leave there."""
    assert mod.DEFAULT_TOOL == "finchat_status"


# --- error reporting ----------------------------------------------------------
def test_a_taskgroup_failure_is_flattened_to_the_useful_line():
    """The MCP client runs inside a TaskGroup. Unwrapped, a 401 reads as "unhandled
    errors in a TaskGroup (1 sub-exception)" and the status — the only fact worth
    having — never reaches the caller."""
    spec = importlib.util.spec_from_file_location("harness_raw", HERE / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # A stand-in rather than a real BaseExceptionGroup: that is 3.11+, this repo's
    # local interpreter is 3.10, and the flattening only ever looks at `.exceptions`.
    class _Group(Exception):
        exceptions = [RuntimeError(
            "Client error '401 Unauthorized' for url 'https://x/mcp'")]

    lines = module._causes(_Group("unhandled errors in a TaskGroup"))
    assert any("401" in line for line in lines), lines


def test_a_cause_chain_is_followed_too():
    spec = importlib.util.spec_from_file_location("harness_raw2", HERE / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        try:
            raise ValueError("the real reason")
        except ValueError as exc:
            raise RuntimeError("the wrapper") from exc
    except RuntimeError as exc:
        lines = module._causes(exc)
    assert any("the real reason" in line for line in lines), lines


# --- the image ----------------------------------------------------------------
def test_the_image_ships_everything_the_handler_imports():
    """A missing COPY fails at the first invocation, not at build — the worst place to
    find out, and the exact fault mcp_server/test_mcp_image.py exists to prevent."""
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    for needed in ("handler.py", "requirements.txt", "aws-credentials.json"):
        assert needed in dockerfile, f"{needed} is not copied into the image"
    assert "GOOGLE_APPLICATION_CREDENTIALS" in dockerfile


def test_the_client_is_pinned_to_the_same_sdk_as_the_server():
    """A client and the server it talks to drifting apart is a protocol bug that shows up
    as an unexplained transport failure."""
    ours = (HERE / "requirements.txt").read_text(encoding="utf-8")
    theirs = (HERE / "../../mcp_server/requirements.txt").read_text(encoding="utf-8")
    for package in ("mcp==", "google-auth=="):
        mine = [l for l in ours.splitlines() if l.startswith(package)]
        yours = [l for l in theirs.splitlines() if l.startswith(package)]
        assert mine and yours and mine[0] == yours[0], (
            f"{package} differs: harness {mine} vs mcp_server {yours}")
