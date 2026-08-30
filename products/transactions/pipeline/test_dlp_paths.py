"""Guards on the DLP resource paths (ADR-0026).

The templates moved from global to regional so that Model Armor can reference the same pair
for chat screening. That move is invisible until runtime: a regional template addressed
through a global parent is not "wrong-looking", it is simply not found, and the pipeline
would drop to un-de-identified counterparty accounts reaching Silver. These tests pin both
halves of the path — the template name and the request parent — because nothing else does.

Offline: no GCP, no DLP client, no network.
"""
import pytest

try:
    from pipeline import MaybeDeidentify
except Exception as _e:  # beam absent, or a broken pyarrow/beam pairing locally
    pytest.skip(f"apache-beam unavailable: {_e}", allow_module_level=True)

PROJECT = "strongsville-city-schools"
LOCATION = "us-central1"


def _fn(deid="finchat-dev-pii-deid", inspect="finchat-dev-pii-inspect"):
    return MaybeDeidentify(PROJECT, deid, inspect, sample_rate=0.0, location=LOCATION)


def test_bare_id_expands_to_a_regional_name():
    fn = _fn()
    assert fn.deid_template == (
        f"projects/{PROJECT}/locations/{LOCATION}/deidentifyTemplates/finchat-dev-pii-deid")
    assert fn.inspect_template == (
        f"projects/{PROJECT}/locations/{LOCATION}/inspectTemplates/finchat-dev-pii-inspect")


def test_expanded_name_is_never_global():
    """The old shape. Model Armor's advanced_config rejects it outright."""
    fn = _fn()
    assert f"projects/{PROJECT}/deidentifyTemplates/" not in fn.deid_template
    assert "/locations/" in fn.deid_template
    assert "/locations/" in fn.inspect_template


def test_a_full_name_from_terraform_is_passed_through_untouched():
    """The terraform output is already locations-qualified; double-prefixing would 404."""
    full = f"projects/{PROJECT}/locations/{LOCATION}/deidentifyTemplates/x"
    fn = MaybeDeidentify(PROJECT, full, "", sample_rate=0.0, location=LOCATION)
    assert fn.deid_template == full


def test_request_parent_matches_the_template_location():
    """The parent and the template must agree on location, or DLP reports not-found."""
    fn = _fn()
    parent = f"projects/{fn.project}/locations/{fn.location}"
    assert fn.deid_template.startswith(parent + "/")
    assert fn.inspect_template.startswith(parent + "/")


def test_location_is_configurable_not_hardcoded():
    fn = MaybeDeidentify(PROJECT, "t", "i", sample_rate=0.0, location="europe-west1")
    assert "/locations/europe-west1/" in fn.deid_template


def test_empty_template_stays_empty():
    """DLP is optional and sampled; an unset template must not become a bogus path."""
    fn = MaybeDeidentify(PROJECT, "", "", sample_rate=0.0, location=LOCATION)
    assert fn.deid_template == ""
    assert fn.inspect_template == ""
