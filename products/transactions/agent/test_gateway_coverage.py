"""Guard: no model call in this package reaches Vertex without trying the gateway first.

DRIFT-4 in `verify_agent_registry.py` checks that registered **agents** transit the
gateway, and it does that by AST-scanning agent definitions for `gateway_model`. A model
call inside a *tool* is invisible to it. That is not hypothetical: `_rerank` called
`genai.Client(...).models.generate_content(...)` directly for weeks while docs/23
reported six call sites and counted none of its traffic — screened by nothing, charged
to no agent, and absent from the denominator that exists to catch exactly this.

So this test covers the gap that check leaves: every direct Vertex call site in the
package must sit in a function that also reaches for the gateway. It deliberately does
not require that direct calls disappear — ADR-0024's fallback is that an unreachable
gateway degrades to a counted direct call rather than taking the product down.
"""
from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATEWAY_NAMES = {"gateway_model", "gateway_llm", "complete"}


def _direct_vertex_calls(tree: ast.AST) -> list[ast.AST]:
    """Nodes constructing a **genai** client — the direct-to-Vertex tell.

    Matching every `.Client(` is too broad: `bigquery.Client(...)` in `_bq_rows` is a
    data call, not a model call, and flagging it would train the reader to ignore this
    test. The receiver has to name genai.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "attr", None) != "Client":
            continue
        recv = node.func.value
        recv_name = getattr(recv, "id", None) or getattr(recv, "attr", None) or ""
        if "genai" in recv_name:
            out.append(node)
    return out


def _enclosing_functions(tree: ast.AST) -> list[ast.FunctionDef]:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _mentions_gateway(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in GATEWAY_NAMES:
            return True
        if isinstance(n, ast.Attribute) and n.attr in GATEWAY_NAMES:
            return True
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names] + [getattr(n, "module", "") or ""]
            if any("gateway" in (x or "") for x in names):
                return True
    return False


def test_every_direct_vertex_call_sits_beside_a_gateway_attempt():
    offenders = []
    for path in sorted(HERE.glob("*.py")):
        if path.name.startswith("test_") or path.name == "gateway_llm.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        direct = _direct_vertex_calls(tree)
        if not direct:
            continue
        for fn in _enclosing_functions(tree):
            fn_direct = _direct_vertex_calls(fn)
            if fn_direct and not _mentions_gateway(fn):
                offenders.append(f"{path.name}::{fn.name}")
    assert not offenders, (
        "these functions call Vertex directly with no gateway attempt in the same "
        f"function: {offenders}. Route through gateway_llm.complete() (ADR-0024); a "
        "direct call is the counted fallback, not the first choice.")


def test_the_reranker_declares_an_identity_and_a_workload_class():
    """Attribution is the point of transiting, not a formality.

    A call that reaches the gateway without an agent id is charged to nobody and shows
    up in no per-agent budget, which is most of what the gateway is for.
    """
    src = (HERE / "tools.py").read_text(encoding="utf-8")
    assert 'agent_id="kb_reranker"' in src
    assert 'workload_class="classification"' in src
    assert "owner=" in src


def test_a_refusal_does_not_fall_through_to_a_direct_call():
    """The one failure mode a gateway must not have.

    Retrying a refusal against Vertex routes around the control in the same request
    that fired it. The reranker's correct response to a refusal is the fusion order.
    """
    src = (HERE / "tools.py").read_text(encoding="utf-8")
    assert "GatewayRefused" in src, "the refusal outcome must be handled by name"
    idx = src.index("GatewayRefused")
    tail = src[idx:idx + 400]
    assert "return candidates" in tail, \
        "a refusal must degrade to the fused order, not proceed to the direct call"
