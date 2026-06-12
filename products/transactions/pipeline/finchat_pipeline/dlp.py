"""DLP component — sampled de-identification of counterparty_account.

Isolated DoFn so the (cost-controlled, API-calling) de-identification stage can be
maintained and reasoned about independently of validation and enrichment.
"""
from __future__ import annotations

import apache_beam as beam


class MaybeDeidentify(beam.DoFn):
    """Sampled DLP de-identification of counterparty_account (cost-controlled).

    A no-op unless a deid_template is configured; samples at `sample_rate` so the
    DLP API spend is bounded. Disabled entirely with sample_rate=0.
    """

    def __init__(self, project, deid_template, inspect_template, sample_rate):
        self.project = project
        # DLP wants FULL resource names; terraform output may give the bare id.
        self.deid_template = self._full(project, "deidentifyTemplates", deid_template)
        self.inspect_template = self._full(project, "inspectTemplates", inspect_template)
        self.sample_rate = sample_rate
        self._client = None

    @staticmethod
    def _full(project, kind, tmpl):
        if tmpl and not tmpl.startswith("projects/"):
            return f"projects/{project}/{kind}/{tmpl}"
        return tmpl

    def setup(self):
        if self.deid_template:
            from google.cloud import dlp_v2
            self._client = dlp_v2.DlpServiceClient()

    def process(self, record):
        import random
        if self._client and record.get("counterparty_account") and random.random() < self.sample_rate:
            req = {
                "parent": f"projects/{self.project}",
                "deidentify_template_name": self.deid_template,
                "item": {"value": str(record["counterparty_account"])},
            }
            # info_type_transformations need an inspect config/template to find PII.
            if self.inspect_template:
                req["inspect_template_name"] = self.inspect_template
            resp = self._client.deidentify_content(request=req)
            record["counterparty_account"] = resp.item.value
        yield record
