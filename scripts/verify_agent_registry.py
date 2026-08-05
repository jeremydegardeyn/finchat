#!/usr/bin/env python3
"""
Agent registry CI gate (ADR-0023).

An inventory that nobody checks is a document. This script is what makes the registry a
control: it reads the agent definitions **out of the source code** and asserts they match
what `agents_catalog.py` declares. Add a tool to an agent without registering it and the
build fails.

Checks
------
  DRIFT-1  every ADK agent constructed in code is registered
  DRIFT-2  every registered agent still exists in code
  DRIFT-3  the tool allow-list in the registry matches the tools passed in code
  REG-1    every agent declares owner, business area, risk tier, identity and data scope
  REG-2    every agent has a distinct service account (no sharing)
  REG-3    consequential agents declare a human-in-the-loop gate
  LIFE-1   no agent is past its recertification date
  PIN-1    production agents run a pinned model snapshot  (warning, not failure)

Exit code 0 = clean, 1 = at least one failure. Warnings never fail the build.

    python scripts/verify_agent_registry.py --env dev
"""
from __future__ import annotations

import argparse
import ast
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agents_catalog import DEFAULT_MODEL_ALIAS, agents, recert_due  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# ADK constructors that create a first-class agent identity. SequentialAgent is a
# control-flow container, not an actor — it holds no tools and takes no action of its
# own, so it is deliberately excluded from the registry.
AGENT_CTORS = {"Agent", "LlmAgent"}


class Findings:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def fail(self, code: str, msg: str) -> None:
        self.failures.append(f"{code}: {msg}")

    def warn(self, code: str, msg: str) -> None:
        self.warnings.append(f"{code}: {msg}")


# --- Source scanning ---------------------------------------------------------

def _literal(node: ast.AST) -> object | None:
    """Best-effort literal extraction; returns None for anything dynamic."""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def scan_adk_agents(path: Path) -> dict[str, list[str]]:
    """Return {agent_name: [tool_names]} for ADK agents constructed in `path`.

    Tools are read from the `tools=[...]` keyword. Entries are bare function references
    (`tools=[get_account_balance]`), which is how ADK takes them, so we read the
    identifier name rather than evaluating.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, list[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        ctor = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if ctor not in AGENT_CTORS:
            continue

        name, tools = None, []
        for kw in node.keywords:
            if kw.arg == "name":
                name = _literal(kw.value)
            elif kw.arg == "tools" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for el in kw.value.elts:
                    if isinstance(el, ast.Name):
                        tools.append(el.id)
                    elif isinstance(el, ast.Attribute):
                        tools.append(el.attr)
        if isinstance(name, str):
            found[name] = sorted(tools)
    return found


def scan_functions(path: Path) -> set[str]:
    """Top-level function names defined in `path` (for non-ADK agent entrypoints)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


# --- Checks ------------------------------------------------------------------

def check_drift(registry: list[dict], f: Findings) -> None:
    """DRIFT-1/2/3 — the registry must match the code."""
    # Group registered agents by the file they are declared to live in.
    by_source: dict[str, list[dict]] = {}
    for a in registry:
        by_source.setdefault(a["source"], []).append(a)

    for source, regs in sorted(by_source.items()):
        path = REPO / source
        if not path.exists():
            f.fail("DRIFT-2", f"{source} does not exist "
                              f"(registered: {', '.join(r['id'] for r in regs)})")
            continue

        adk = scan_adk_agents(path)
        funcs = scan_functions(path)

        for a in regs:
            code_name = a["code_name"]
            if code_name in adk:
                declared = sorted(a["tools"])
                actual = adk[code_name]
                if declared != actual:
                    extra = sorted(set(actual) - set(declared))
                    missing = sorted(set(declared) - set(actual))
                    detail = []
                    if extra:
                        detail.append(f"code grants un-registered tools {extra}")
                    if missing:
                        detail.append(f"registry declares tools not in code {missing}")
                    f.fail("DRIFT-3", f"{a['id']} ({source}): " + "; ".join(detail))
            elif code_name in funcs:
                pass  # non-ADK agent entrypoint (steward harness) — existence is the check
            elif a["kind"] == "managed_agent":
                pass  # managed resource; no local constructor to compare against
            else:
                f.fail("DRIFT-2", f"{a['id']}: code_name '{code_name}' not found in {source}")

        # Anything constructed in code but absent from the registry.
        registered_names = {r["code_name"] for r in regs}
        for name in sorted(adk):
            if name not in registered_names:
                f.fail("DRIFT-1", f"unregistered agent '{name}' constructed in {source} — "
                                  f"add it to scripts/agents_catalog.py")


def check_registration(registry: list[dict], f: Findings) -> None:
    """REG-1/2/3 — completeness and identity."""
    required = ("owner", "business_area", "risk_tier", "sa_key", "data_scope")
    seen_sa: dict[str, str] = {}

    for a in registry:
        for field in required:
            if not a.get(field):
                f.fail("REG-1", f"{a['id']} is missing '{field}'")

        sa = a.get("sa_key")
        if sa:
            if sa in seen_sa:
                f.fail("REG-2", f"{a['id']} shares service account '{sa}' with "
                                f"{seen_sa[sa]} — every agent needs a distinct identity")
            else:
                seen_sa[sa] = a["id"]

        if a.get("consequential") and not a.get("hitl"):
            f.fail("REG-3", f"{a['id']} takes consequential action but declares no "
                            f"human-in-the-loop gate")


def check_lifecycle(registry: list[dict], f: Findings, today: date) -> None:
    """LIFE-1 — recertification, on the privileged-access cycle."""
    for a in registry:
        due = recert_due(a)
        if due < today:
            f.fail("LIFE-1", f"{a['id']} recertification overdue since {due.isoformat()} "
                             f"(owner {a['owner']})")
        elif (due - today).days <= 14:
            f.warn("LIFE-1", f"{a['id']} recertification due {due.isoformat()} "
                             f"({(due - today).days}d) — owner {a['owner']}")


def check_pinning(registry: list[dict], f: Findings) -> None:
    """PIN-1 — model version pinning (ADR-0022). Warning, not a failure.

    Running an alias is a legitimate posture; running one without having decided to is
    not. The warning exists so the choice is visible in every build log.
    """
    for a in registry:
        alias = a.get("model_alias")
        if alias == DEFAULT_MODEL_ALIAS:
            f.warn("PIN-1", f"{a['id']} runs the floating alias '{alias}' rather than a "
                            f"pinned snapshot — provider-side changes can alter behaviour "
                            f"without notice")


# --- Entrypoint --------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Verify the FinChat agent registry against code.")
    p.add_argument("--env", default="dev")
    p.add_argument("--today", help="override today's date (ISO) for testing")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = p.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    registry = [a for a in agents(args.env) if a["status"] == "active"]
    f = Findings()

    check_drift(registry, f)
    check_registration(registry, f)
    check_lifecycle(registry, f, today)
    check_pinning(registry, f)

    print(f"Agent registry verification — {args.env} ({len(registry)} active agents)\n")

    for w in f.warnings:
        print(f"  WARN  {w}")
    for fail in f.failures:
        print(f"  FAIL  {fail}")

    if not f.failures and not f.warnings:
        print("  clean — registry matches code, all agents owned and in certification")

    print()
    if f.failures:
        print(f"{len(f.failures)} failure(s), {len(f.warnings)} warning(s)")
        return 1
    if args.strict and f.warnings:
        print(f"0 failures, {len(f.warnings)} warning(s) — failing due to --strict")
        return 1
    print(f"0 failures, {len(f.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
