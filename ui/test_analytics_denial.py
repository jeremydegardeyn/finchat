"""Guard: an analytics denial must name the right cause, and the right owner.

A Loan Approver asked "how many customers per segment" and was told they did not have
access to the analyst data products, with a link to request access from the data product
owner. That question reads `dim_customer.segment` and `customer_id`, neither of which
carries a policy tag, and the rejection arrived as an HTTP 403 on the Conversational
Analytics call itself rather than as a query-time error. The message asserted a cause
nobody had established and pointed at an owner who could not have fixed it: a Dataplex
access request grants BigQuery reader roles, not `geminidataanalytics.dataAgentUser`.

The old branch also discarded the response body, so the one failure where the reason
mattered was the one failure that recorded nothing. These tests pin both halves: the
message follows the evidence, and the evidence is kept.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analytics_denial  # noqa: E402


def test_a_bigquery_denial_is_named_as_data_access_and_sent_to_the_product_owner():
    out = analytics_denial.analytics_denied(403, "PERMISSION_DENIED: user does not have "
                                        "bigquery.tables.getData on dim_customer")
    assert out["denial"] == "data"
    assert "data product owner" in out["error"]
    assert out["action"] == "request_access"
    assert out.get("request_url")


def test_a_policy_tag_denial_is_named_as_data_access():
    out = analytics_denial.analytics_denied(403, "Access Denied: policy tag pii_financial on "
                                        "column amount requires fineGrainedReader")
    assert out["denial"] == "data"
    assert "data product owner" in out["error"]


def test_a_missing_api_entitlement_does_not_blame_the_data_product_owner():
    """The failure the screenshot showed. Nothing a data-product owner grants fixes it,
    so the message must not send the user to one, and must not offer a request link."""
    out = analytics_denial.analytics_denied(
        403, "PERMISSION_DENIED: caller lacks geminidataanalytics.dataAgents.chat")
    assert out["denial"] == "entitlement"
    assert "platform team" in out["error"].lower()
    # It may NAME the data product owner in order to rule them out, which is the useful
    # thing to say here; what it must not do is route the user to them.
    assert "request access from the data product owner" not in out["error"].lower()
    assert "request_url" not in out
    assert out.get("action") != "request_access"


def test_a_401_is_reported_as_a_session_problem_not_a_data_problem():
    """401 is the token, not the entitlement: re-signing in can fix it, and telling the
    user to request data access instead would send them somewhere that cannot help."""
    out = analytics_denial.analytics_denied(401, "UNAUTHENTICATED: invalid authentication credential")
    assert out["denial"] == "identity"
    assert "sign in again" in out["error"].lower()
    assert "data product owner" not in out["error"]


def test_the_reason_is_always_kept():
    """Whatever branch is taken, the body survives. Losing it is what made the original
    report unanswerable from the code alone."""
    for status, body in ((403, "policy tag denied"), (403, "no entitlement"),
                         (401, "bad credential")):
        assert body in analytics_denial.analytics_denied(status, body)["detail"]


def test_an_empty_body_still_produces_a_message_rather_than_an_exception():
    """A 403 with no body is plausible and must not be worse than a 403 with one."""
    out = analytics_denial.analytics_denied(403, "")
    assert out["error"] and out["denial"] == "entitlement"
    assert out["detail"] == ""


def test_the_detail_is_bounded():
    out = analytics_denial.analytics_denied(403, "x" * 5000)
    assert len(out["detail"]) == 400
