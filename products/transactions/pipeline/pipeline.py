"""
FinChat transactions streaming pipeline (Apache Beam / Dataflow).

Flow:  Pub/Sub subscription
         -> parse + schema enforcement   (invalid -> DLQ topic)
         -> optional DLP de-identification (sampled, cost-controlled)
         -> enrich (lineage columns)
         -> BigQuery Silver.transaction   (insertId = idempotency_key -> dedup)

Run modes:
  * Dataflow (streaming, Flex Template): reads --input_subscription.
  * Local/offline (DirectRunner, batch):  reads --input_file (JSON lines),
    writes valid records to --output_file and failures to --dlq_file.
    -> lets you exercise the full transform graph with no GCP.

ADR-0003: packaged as a Flex Template, launched on-demand and drained by default.
"""
from __future__ import annotations

import argparse
import logging
import os

import apache_beam as beam
from apache_beam.options.pipeline_options import (
    PipelineOptions, StandardOptions, GoogleCloudOptions)

from transforms import parse_and_validate, enrich, to_dlq_envelope, ValidationError

VALID = "valid"
DLQ = "dlq"

SILVER_SCHEMA = {
    "fields": [
        {"name": "transaction_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "idempotency_key", "type": "STRING", "mode": "REQUIRED"},
        {"name": "account_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "txn_type", "type": "STRING", "mode": "REQUIRED"},
        {"name": "amount", "type": "NUMERIC", "mode": "REQUIRED"},
        {"name": "currency", "type": "STRING", "mode": "REQUIRED"},
        {"name": "counterparty_account", "type": "STRING", "mode": "NULLABLE"},
        {"name": "status", "type": "STRING", "mode": "REQUIRED"},
        {"name": "event_time", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "ingest_time", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "source_system", "type": "STRING", "mode": "NULLABLE"},
        {"name": "pipeline_version", "type": "STRING", "mode": "NULLABLE"},
    ]
}


class ParseAndValidate(beam.DoFn):
    """Tagged-output DoFn: valid records on main, failures on DLQ."""

    def process(self, raw):
        try:
            yield parse_and_validate(raw)
        except ValidationError as e:
            yield beam.pvalue.TaggedOutput(DLQ, to_dlq_envelope(raw, str(e)))


class MaybeDeidentify(beam.DoFn):
    """Sampled DLP de-identification of free-text/PII fields (cost-controlled).

    In the Silver model the direct identifiers live on customer/account; here we
    defensively scrub counterparty_account when DLP is enabled. Sampling keeps
    sandbox DLP cost near zero while proving the pattern.
    """

    def __init__(self, project, deid_template, sample_rate):
        self.project = project
        self.deid_template = deid_template
        self.sample_rate = sample_rate
        self._client = None

    def setup(self):
        if self.deid_template:
            from google.cloud import dlp_v2
            self._client = dlp_v2.DlpServiceClient()

    def process(self, record):
        import random
        if self._client and record.get("counterparty_account") and random.random() < self.sample_rate:
            parent = f"projects/{self.project}"
            resp = self._client.deidentify_content(
                request={
                    "parent": parent,
                    "deidentify_template_name": self.deid_template,
                    "item": {"value": str(record["counterparty_account"])},
                }
            )
            record["counterparty_account"] = resp.item.value
        yield record


def build_pipeline(p, opts):
    if opts.input_file:  # local/offline batch mode
        raw = p | "ReadFile" >> beam.io.ReadFromText(opts.input_file)
    else:  # streaming from Pub/Sub
        raw = p | "ReadPubSub" >> beam.io.ReadFromPubSub(subscription=opts.input_subscription)

    parsed = raw | "ParseValidate" >> beam.ParDo(ParseAndValidate()).with_outputs(DLQ, main=VALID)

    valid = (
        parsed[VALID]
        | "Deidentify" >> beam.ParDo(MaybeDeidentify(opts.project, opts.deid_template, opts.dlp_sample_rate))
        | "Enrich" >> beam.Map(enrich)
    )

    # --- sink: valid -> BigQuery Silver (insertId dedup via idempotency_key) ---
    if opts.output_file:  # offline
        import json as _json
        (
            valid
            | "ValidToJson" >> beam.Map(_json.dumps)
            | "WriteValidText" >> beam.io.WriteToText(opts.output_file, file_name_suffix=".jsonl")
        )
    else:
        valid | "WriteSilver" >> beam.io.WriteToBigQuery(
            table=opts.output_table,
            schema=SILVER_SCHEMA,
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND,
            create_disposition=beam.io.BigQueryDisposition.CREATE_NEVER,
            method=beam.io.WriteToBigQuery.Method.STREAMING_INSERTS,
            # Best-effort exactly-once: BigQuery dedups on insertId within the window.
            kms_key=None,
        )

    # --- sink: failures -> DLQ -------------------------------------------------
    if opts.dlq_file:  # offline
        parsed[DLQ] | "WriteDlqText" >> beam.io.WriteToText(opts.dlq_file, file_name_suffix=".jsonl")
    elif opts.dlq_topic:
        parsed[DLQ] | "WriteDlq" >> beam.io.WriteToPubSub(topic=opts.dlq_topic)


def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--region")
    parser.add_argument("--input_subscription")
    parser.add_argument("--output_table", help="project:dataset.table for Silver.transaction")
    parser.add_argument("--dlq_topic")
    parser.add_argument("--deid_template", default="", help="DLP de-identify template name (optional)")
    parser.add_argument("--dlp_sample_rate", type=float, default=0.1)
    # offline mode
    parser.add_argument("--input_file")
    parser.add_argument("--output_file")
    parser.add_argument("--dlq_file")
    known, beam_args = parser.parse_known_args(argv)

    options = PipelineOptions(beam_args)
    # No save_main_session: our transforms ship in the worker container image
    # (see Dockerfile), so workers import them normally — pickling __main__ is
    # both unnecessary and was the cause of LoadMainSessionException.
    if not known.input_file:
        options.view_as(StandardOptions).streaming = True
        # The Flex Template launcher passes --project/--region, but argparse above
        # consumes them out of beam_args; re-apply so the Dataflow runner sees them.
        gcp = options.view_as(GoogleCloudOptions)
        gcp.project = known.project or gcp.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if known.region or not gcp.region:
            gcp.region = known.region or gcp.region or os.environ.get("REGION", "us-central1")

    with beam.Pipeline(options=options) as p:
        build_pipeline(p, known)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    run()
