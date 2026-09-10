"""Guard: a service that uses google-auth's requests transport must depend on requests."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def _requirements_files():
    """Every deployable unit in the repo, not a hand-kept list.

    The first version of this guard scanned `products/experience` and `products/process`
    — the two directories I had just fixed. `mcp_server/` has the same import and the
    same gap, and would have shipped it: a guard scoped to the place a bug was found
    only catches that bug.
    """
    for req in REPO.rglob("requirements.txt"):
        if not SKIP.intersection(req.relative_to(REPO).parts):
            yield req


def test_google_auth_requests_transport_has_its_dependency():
    """`google.auth.transport.requests` imports the `requests` package lazily.

    Installing google-auth alone is enough to import the module and NOT enough to use
    it: the first token mint raises ImportError, the helper returns None, and the call
    goes out unauthenticated. That shipped — the process API reported its backends as
    unavailable when the real fault was a missing dependency in its own image.

    Most of these services do get `requests` today, transitively, because a
    `google-cloud-*` client pulls `google-api-core`, which requires it. That is luck
    rather than a dependency: it disappears the moment a service drops its BigQuery
    client, which is exactly what LAYER-2 asks the process layer to do. A package you
    import directly is a package you declare.
    """
    offenders = []
    for req in _requirements_files():
        src = "\n".join(p.read_text(encoding="utf-8")
                        for p in req.parent.rglob("*.py") if not p.name.startswith("test_"))
        if "google.auth.transport.requests" not in src:
            continue
        if "requests==" not in req.read_text(encoding="utf-8"):
            offenders.append(str(req.relative_to(REPO)).replace("\\", "/"))
    assert not offenders, (
        f"{offenders} use google.auth.transport.requests but do not pin `requests`. "
        "google-auth alone imports fine and fails at the first token mint.")


# --- deploy-time configuration ------------------------------------------------
# A different shape of the same fault: a module reads configuration the deploy never
# passes. Found on the MCP service, where caller.py resolves staff personas from
# APPROVER_EMAILS / ANALYST_EMAILS / ADMIN_EMAILS and its docstring says those are "the
# same lists the web BFF resolves personas from, so the two channels cannot drift". The
# deploy passed them to the UI and not to the MCP service, so the lists were empty,
# every user resolved to "not staff", and the staff gate refused everyone.
#
# It failed CLOSED, which is why nothing broke visibly — the tool was simply inert, and
# a gate that refuses everyone looks identical to a gate that is working.

def _mcp_deploy_command() -> str:
    """The `deploy mcp ...` line from the build workflow."""
    workflow = (REPO / ".github" / "workflows" / "build-deploy.yml").read_text(
        encoding="utf-8")
    lines = [ln for ln in workflow.splitlines() if "deploy mcp mcp mcp" in ln]
    assert len(lines) == 1, f"expected one mcp deploy line, found {len(lines)}"
    return lines[0]


def test_the_mcp_deploy_passes_every_email_list_caller_py_reads():
    """Derived from the source, not listed here, so a fourth list cannot repeat this."""
    import re

    caller = (REPO / "mcp_server" / "caller.py").read_text(encoding="utf-8")
    wanted = set(re.findall(r'_emails\("([A-Z_]+)"\)', caller))
    assert wanted, "no _emails() calls found — has caller.py changed shape?"

    command = _mcp_deploy_command()
    missing = sorted(name for name in wanted if f"{name}=" not in command)
    assert not missing, (
        f"caller.py reads {missing} but the MCP deploy never sets them. The lists come "
        f"back empty, nobody resolves as staff, and require_staff refuses every caller "
        f"— silently, because failing closed looks like working.")


def test_the_mcp_deploy_separates_service_callers_with_semicolons():
    """`gcloud run deploy --set-env-vars` uses the comma as ITS delimiter, so a
    comma-separated list of emails is parsed as separate variables and the deploy fails
    with "Bad syntax for dict arg" naming the second email rather than the reason."""
    command = _mcp_deploy_command()
    if "FINCHAT_MCP_SERVICE_CALLERS=" not in command:
        return
    value = command.split("FINCHAT_MCP_SERVICE_CALLERS=", 1)[1]
    value = value.split(",")[0]          # a comma here ends the variable
    assert "@" in value or "{{" in value, (
        "FINCHAT_MCP_SERVICE_CALLERS looks truncated at a comma — the list must use ';'")
