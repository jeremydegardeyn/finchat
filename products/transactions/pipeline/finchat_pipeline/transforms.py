"""Beam transform component — thin DoFn wrappers over the pure validation logic.

Keeping the Beam wiring here (and the pure logic in validation.py) means
validation stays runner-free and testable, while this module owns the
tagged-output routing (valid -> main, schema failure -> DLQ).
"""
from __future__ import annotations

import apache_beam as beam

from finchat_pipeline.validation import (
    ValidationError, parse_and_validate, to_dlq_envelope)

# PCollection output tags shared with the pipeline graph.
VALID = "valid"
DLQ = "dlq"


class ParseAndValidate(beam.DoFn):
    """Tagged-output DoFn: valid records on the main output, schema failures
    (enveloped with their reason) on the DLQ tag."""

    def process(self, raw):
        try:
            yield parse_and_validate(raw)
        except ValidationError as e:
            yield beam.pvalue.TaggedOutput(DLQ, to_dlq_envelope(raw, str(e)))
