"""
Guards on the DLP resource paths (ADR-0026).

The templates moved from global to regional so that Model Armor can reference the same pair
for chat screening. That move is invisible until runtime: a regional template addressed
through a global parent is not "wrong-looking", it is simply not found, and the pipeline
would drop to un-de-identified counterparty accounts reaching Silver. These tests pin both
halves of the path — the template name and the request parent — because nothing else does.

Exercises `transforms.dlp_template_path`, which is Beam-free. `pipeline.py` carries an
inlined copy (it is single-file by design, so the Flex Template can ship it to stock Beam
workers); `test_pipeline_mirrors_transforms` below is what keeps the two honest.
"""
from pathlib import Path

from transforms import dlp_template_path

HERE = Path(__file__).resolve().parent

PROJECT = "strongsville-city-schools"
LOCATION = "us-central1"


class _Fn:
    """Stands in for MaybeDeidentify's path construction without importing Beam."""

    def __init__(self, deid="finchat-dev-pii-deid", inspect="finchat-dev-pii-inspect",
                 project=PROJECT, location=LOCATION):
        self.project, self.location = project, location
        self.deid_template = dlp_template_path(project, location, "deidentifyTemplates", deid)
        self.inspect_template = dlp_template_path(project, location, "inspectTemplates", inspect)


def _fn(deid="finchat-dev-pii-deid", inspect="finchat-dev-pii-inspect"):
    return _Fn(deid, inspect)


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
    fn = _Fn(full, "")
    assert fn.deid_template == full


def test_request_parent_matches_the_template_location():
    """The parent and the template must agree on location, or DLP reports not-found."""
    fn = _fn()
    parent = f"projects/{fn.project}/locations/{fn.location}"
    assert fn.deid_template.startswith(parent + "/")
    assert fn.inspect_template.startswith(parent + "/")


def test_location_is_configurable_not_hardcoded():
    fn = _Fn("t", "i", location="europe-west1")
    assert "/locations/europe-west1/" in fn.deid_template


def test_empty_template_stays_empty():
    """DLP is optional and sampled; an unset template must not become a bogus path."""
    fn = _Fn("", "")
    assert fn.deid_template == ""
    assert fn.inspect_template == ""


def test_pipeline_mirrors_transforms():
    """pipeline.py inlines this function so the Flex Template ships a single file. An
    import there would NameError on Dataflow while passing every local test, so the copy
    is deliberate — and this is what stops the two drifting apart."""
    src = (HERE / "pipeline.py").read_text(encoding="utf-8")
    assert "def dlp_template_path(" in src, "pipeline.py lost its inlined copy"
    assert 'f"projects/{project}/locations/{location}/{kind}/{template}"' in src
    assert "from transforms import" not in src, "pipeline.py must stay single-file"
