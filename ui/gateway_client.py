"""Enterprise AI Gateway client — the governed path to a model (ADR-0024).

Every LLM call site in FinChat should reach a model through here rather than calling
Vertex directly, so that identity, PII screening, tier routing, token budgets and audit
are consumed rather than reimplemented per call site.

Two design points worth stating plainly:

**Fallback is direct-to-Vertex, and it is counted.** If `AI_GATEWAY_URL` is unset or the
gateway is unreachable, the call still succeeds by going straight to Vertex — a governance
layer that takes the product down when it hiccups gets removed within a quarter. But every
such call is recorded as a bypass, because the honest measure of a gateway programme is the
share of traffic that actually transits it, and a bypass you don't count is a bypass you
will report as compliance.

**Unless AI_GATEWAY_REQUIRED is set, in which case there is no fallback.** Counting a bypass
tells you the control was skipped; it does not stop the skipping, and a chokepoint you can
step around by unsetting an environment variable is a convention rather than an enforcement.
With the flag on, an unconfigured or unreachable gateway raises GatewayUnavailable instead of
reaching Vertex, so the screening the gateway performs cannot be bypassed by configuration.
The cost is availability: a gateway outage takes the agent down. That is the correct trade
on a regulated path and the wrong one in a sandbox, which is why it is a flag and why it is
off by default.

**Not every call site can use this.** ADK agents call the model inside the framework, so
routing them requires an ADK `BaseLlm` adapter rather than an HTTP client. Those call sites
are recorded as structural bypasses with a reason, not quietly omitted from the denominator.
See docs/23 for the current transit share and what remains.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "").rstrip("/")
GATEWAY_TIMEOUT = float(os.getenv("AI_GATEWAY_TIMEOUT", "45"))
# Enforced chokepoint. Off, a missing or unreachable gateway degrades to a counted
# direct call; on, it raises. See GatewayUnavailable for the trade.
GATEWAY_REQUIRED = os.getenv("AI_GATEWAY_REQUIRED", "").lower() in ("1", "true", "yes")

# Bypass counters, in-process. Exported at /api/gateway/transit so the share is
# observable without a BigQuery round-trip; the durable record is the audit log.
_lock = threading.Lock()
_counters: dict[str, int] = {"transited": 0, "bypass_unconfigured": 0,
                             "bypass_error": 0, "blocked": 0}


def _count(key: str) -> None:
    with _lock:
        _counters[key] = _counters.get(key, 0) + 1


def counters() -> dict:
    with _lock:
        c = dict(_counters)
    total = c["transited"] + c["bypass_unconfigured"] + c["bypass_error"]
    c["total"] = total
    c["transit_share"] = round(c["transited"] / total, 3) if total else None
    c["configured"] = bool(GATEWAY_URL)
    return c


def _id_token(audience: str) -> str | None:
    """OIDC token for the private gateway. None locally, where it runs unauthenticated."""
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token as gid
        return gid.fetch_id_token(google.auth.transport.requests.Request(), audience)
    except Exception:
        return None


class GatewayBlocked(Exception):
    """The gateway refused the call — PII, budget, or an unregistered workload.

    Deliberately a distinct exception from a transport failure: a refusal is the control
    working and must NOT fall back to a direct call, whereas an unreachable gateway is an
    availability problem that should. Collapsing the two would let a PII block be silently
    retried around the control.
    """

    def __init__(self, outcome: str, detail: dict):
        super().__init__(f"gateway refused: {outcome}")
        self.outcome = outcome
        self.detail = detail


class GatewayUnavailable(Exception):
    """The gateway could not be reached, and this deployment forbids bypassing it.

    Only raised when GATEWAY_REQUIRED is set. Without it, an unconfigured or unreachable
    gateway degrades to a counted direct call, which keeps a sandbox working when the
    gateway is down. That is the right default for a demo and the wrong one for a
    regulated path, because screening you can route around by unsetting an environment
    variable is a convention rather than a control (ADR-0024, ADR-0026).

    The trade is explicit: with this on, a gateway outage takes the agent down instead of
    quietly running it ungoverned. That is the intended behaviour — an ungoverned answer
    is worse than no answer where the control is the point.
    """


def complete(prompt: str, *, agent_id: str, workload_class: str,
             owner: str | None = None, session_id: str | None = None,
             tier: str | None = None, max_output_tokens: int | None = None,
             on_behalf_of: str | None = None,
             routing_text: str | None = None) -> dict | None:
    """Governed completion. Returns the gateway payload, or None to fall back.

    Raises GatewayBlocked when the gateway refused on policy grounds — callers must let
    that propagate rather than retrying directly against Vertex.
    """
    if not GATEWAY_URL:
        _count("bypass_unconfigured")
        if GATEWAY_REQUIRED:
            raise GatewayUnavailable("gateway required but AI_GATEWAY_URL is unset")
        return None

    body = json.dumps({
        "agent_id": agent_id, "workload_class": workload_class, "prompt": prompt,
        "owner": owner, "session_id": session_id, "tier": tier,
        "max_output_tokens": max_output_tokens,
        # Present => the spend is charged to the PERSON, not the shared agent id.
        # analyst_intent_router serves every analyst; billing them collectively means
        # one heavy user silently eats everyone else's allowance.
        "on_behalf_of": on_behalf_of,
        # Tier selection reads this instead of the full prompt. A grounded prompt is long
        # because of its CONTEXT; routing on the assembled payload billed every semantics
        # question at premium rates.
        "routing_text": routing_text,
    }).encode()
    headers = {"Content-Type": "application/json"}
    tok = _id_token(GATEWAY_URL)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    req = urllib.request.Request(f"{GATEWAY_URL}/v1/complete", data=body,
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=GATEWAY_TIMEOUT) as r:
            payload = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        _count("bypass_error")
        if GATEWAY_REQUIRED:
            raise GatewayUnavailable(f"gateway unreachable: {type(e).__name__}") from e
        return None

    outcome = payload.get("outcome")
    if outcome == "ok":
        _count("transited")
        return payload

    if outcome in ("pii_blocked", "budget_exceeded", "unregistered_workload"):
        _count("blocked")
        raise GatewayBlocked(outcome, payload)

    # model_error and anything unrecognised: the gateway reached a decision but could not
    # serve. Treat as availability, not policy — fall back and count the bypass.
    _count("bypass_error")
    if GATEWAY_REQUIRED:
        raise GatewayUnavailable(f"gateway could not serve: {outcome}")
    return None
