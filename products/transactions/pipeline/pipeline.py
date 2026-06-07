"""
FinChat transactions streaming pipeline (Apache Beam / Dataflow).

Flow:  Pub/Sub subscription
         -> parse + schema enforcement   (invalid -> DLQ topic)
         -> optional DLP de-identification (sampled, cost-controlled)
         -> enrich (lineage columns)
         -> BigQuery Silver.transaction   (insertId = idempotency_key -> dedup)

SINGLE-FILE BY DESIGN: the transform logic is inlined here (it mirrors
transforms.py, which the unit tests import without Beam). Inlining means every
DoFn/function lives in __main__, so with save_main_session=True the code ships to
the stock Beam SDK workers — no custom worker container or extra module needed.
Keep the inlined functions in sync with transforms.py.

Run modes:
  * Dataflow (streaming, Flex Template): reads --input_subscription.
  * Local/offline (DirectRunner, batch):  reads --input_file (JSON lines).

ADR-0003: packaged as a Flex Template, launched on-demand and drained by default.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.options.pipeline_options import (
    PipelineOptions, StandardOptions, GoogleCloudOptions, SetupOptions)

# --------------------------------------------------------------------------- #
# Inlined transform logic (mirrors transforms.py — keep in sync)
# --------------------------------------------------------------------------- #
PIPELINE_VERSION = "1.0.0"
VALID_TYPES = {"DEPOSIT", "WITHDRAWAL", "TRANSFER", "FEE"}
VALID_STATUS = {"POSTED", "PENDING", "REJECTED"}
REQUIRED = ("transaction_id", "idempotency_key", "account_id", "txn_type",
            "amount", "currency", "status", "event_time")
_AMOUNT_RE = re.compile(r"^[0-9]+(\.[0-9]{1,2})?$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")


class ValidationError(ValueError):
    """Raised when a message fails schema enforcement."""


def parse_and_validate(raw):
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValidationError(f"unparseable payload: {e}") from e
    if not isinstance(obj, dict):
        raise ValidationError("payload is not a JSON object")
    missing = [f for f in REQUIRED if f not in obj or obj[f] in (None, "")]
    if missing:
        raise ValidationError(f"missing required fields: {missing}")
    if obj["txn_type"] not in VALID_TYPES:
        raise ValidationError(f"invalid txn_type: {obj['txn_type']}")
    if obj["status"] not in VALID_STATUS:
        raise ValidationError(f"invalid status: {obj['status']}")
    if not _AMOUNT_RE.match(str(obj["amount"])):
        raise ValidationError(f"invalid amount format: {obj['amount']}")
    if not _CCY_RE.match(str(obj["currency"])):
        raise ValidationError(f"invalid currency: {obj['currency']}")
    try:
        datetime.fromisoformat(str(obj["event_time"]).replace("Z", "+00:00"))
    except ValueError as e:
        raise ValidationError(f"invalid event_time: {obj['event_time']}") from e
    return {
        "transaction_id": obj["transaction_id"],
        "idempotency_key": obj["idempotency_key"],
        "account_id": obj["account_id"],
        "txn_type": obj["txn_type"],
        "amount": float(obj["amount"]),
        "currency": obj["currency"],
        "counterparty_account": obj.get("counterparty_account"),
        "status": obj["status"],
        "event_time": str(obj["event_time"]).replace("Z", "+00:00"),
    }


def enrich(record, source_system="synthetic-generator"):
    record = dict(record)
    record["ingest_time"] = datetime.now(timezone.utc).isoformat()
    record["source_system"] = source_system
    record["pipeline_version"] = PIPELINE_VERSION
    return record


def to_dlq_envelope(raw, error):
    payload = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return json.dumps({
        "error": error, "payload": payload,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
    }).encode("utf-8")


# --------------------------------------------------------------------------- #
# Beam graph
# --------------------------------------------------------------------------- #
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
    """Sampled DLP de-identification of counterparty_account (cost-controlled)."""

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
            resp = self._client.deidentify_content(request={
                "parent": f"projects/{self.project}",
                "deidentify_template_name": self.deid_template,
                "item": {"value": str(record["counterparty_account"])},
            })
            record["counterparty_account"] = resp.item.value
        yield record


def build_pipeline(p, opts):
    if opts.input_file:
        raw = p | "ReadFile" >> beam.io.ReadFromText(opts.input_file)
    else:
        raw = p | "ReadPubSub" >> beam.io.ReadFromPubSub(subscription=opts.input_subscription)

    parsed = raw | "ParseValidate" >> beam.ParDo(ParseAndValidate()).with_outputs(DLQ, main=VALID)

    valid = (
        parsed[VALID]
        | "Deidentify" >> beam.ParDo(MaybeDeidentify(opts.project, opts.deid_template, opts.dlp_sample_rate))
        | "Enrich" >> beam.Map(enrich)
    )

    if opts.output_file:
        (valid | "ValidToJson" >> beam.Map(json.dumps)
               | "WriteValidText" >> beam.io.WriteToText(opts.output_file, file_name_suffix=".jsonl"))
    else:
        valid | "WriteSilver" >> beam.io.WriteToBigQuery(
            table=opts.output_table,
            schema=SILVER_SCHEMA,
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND,
            create_disposition=beam.io.BigQueryDisposition.CREATE_NEVER,
            method=beam.io.WriteToBigQuery.Method.STREAMING_INSERTS,
        )

    if opts.dlq_file:
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
    parser.add_argument("--deid_template", default="")
    parser.add_argument("--dlp_sample_rate", type=float, default=0.1)
    parser.add_argument("--input_file")
    parser.add_argument("--output_file")
    parser.add_argument("--dlq_file")
    known, beam_args = parser.parse_known_args(argv)

    options = PipelineOptions(beam_args)
    # Ship this module's code to stock Beam workers (everything is in __main__).
    options.view_as(SetupOptions).save_main_session = True
    if not known.input_file:
        options.view_as(StandardOptions).streaming = True
        # Flex launcher passes --project/--region; argparse consumes them, re-apply.
        gcp = options.view_as(GoogleCloudOptions)
        gcp.project = known.project or gcp.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if known.region or not gcp.region:
            gcp.region = known.region or gcp.region or os.environ.get("REGION", "us-central1")

    with beam.Pipeline(options=options) as p:
        build_pipeline(p, known)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    run()
