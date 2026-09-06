"""Why an analytics request was refused, decided from evidence rather than assumed.

Kept out of server.py so it can be tested without importing FastAPI. Every other UI
module with logic worth pinning — gateway_client, control_events, intent — is standalone
for the same reason, and this one was not until its test failed in CI for exactly that.
"""
from __future__ import annotations

import os

# Where a data-access denial sends the user. Read from the environment here rather than
# imported from server.py, so this module stays free of the app and its dependencies.
REQUEST_ACCESS_URL = ("https://console.cloud.google.com/dataplex/govern/data-products"
                      "?project=" + os.getenv("GCP_PROJECT", ""))

# Signals in a Conversational Analytics error body that mean BigQuery refused the
# caller's own credentials, rather than the API refusing the call outright.
DATA_DENIAL_SIGNALS = ("policy tag", "policytag", "bigquery", "dataset", "table",
                       "column", "masked", "fine-grained", "finegrained")


def analytics_denied(status: int, body: str) -> dict:
    """Explain a 401/403 from the Conversational Analytics call without guessing.

    Two unrelated failures land on these status codes, and they have different owners:

      * The caller holds no `geminidataanalytics` role, so the API refuses before any
        SQL is planned. Only the platform team can grant that; a data-product owner
        cannot, and sending the user to one wastes their time.
      * The caller may call the API, but their propagated credentials (ADR-0019) are
        refused against a table or a policy-tagged column. That IS a data-access
        question and the data-product owner is the right person to ask.

    The response body is the only thing that separates them. The previous version
    discarded it and asserted the first case as though it were established, so the one
    failure where the reason mattered was the one failure that recorded nothing. The
    detail is returned now whichever branch is taken; a message that names the wrong
    owner costs more than one that admits what it does not know.
    """
    detail = (body or "")[:400]
    lowered = detail.lower()

    if any(signal in lowered for signal in DATA_DENIAL_SIGNALS):
        return {"mode": "analytics", "action": "request_access", "denial": "data",
                "error": "Your access level doesn't permit reading this data "
                         "(column-level security or table access). Request access from "
                         "the data product owner.",
                "request_url": REQUEST_ACCESS_URL, "detail": detail}

    if status == 401:
        return {"mode": "analytics", "denial": "identity",
                "error": "Your session was rejected by the analytics service. Sign out "
                         "and sign in again; if it persists, the sign-in scope is wrong "
                         "rather than your data access.",
                "detail": detail}

    return {"mode": "analytics", "denial": "entitlement",
            "error": "Your account isn't entitled to use the analytics service. This is "
                     "a platform entitlement, not a data-access problem, so it comes "
                     "from the platform team rather than a data product owner.",
            "detail": detail}
