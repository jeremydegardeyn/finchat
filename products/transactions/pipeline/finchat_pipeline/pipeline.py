"""Beam graph wiring + Flex Template entrypoint for the transactions pipeline.

Composition root: imports the modular components and wires them into

  Pub/Sub subscription
    -> parse + schema enforcement     (invalid -> DLQ)        [transforms + validation]
    -> stateful dedup on idempotency_key (redelivery/retries)
    -> sampled DLP de-identification                          [dlp]
    -> enrich (lineage columns)                               [enrich]
    -> BigQuery Silver.transaction (append)

MODULAR BY DESIGN: the components live in their own modules and ship to workers as
an INSTALLED PACKAGE via a custom container (run with --sdk_container_image=<image>).
That removes the old single-file `save_main_session` constraint — workers import
validation/transforms/dlp/enrich natively, so each can be maintained on its own.

Exactly-once notes unchanged: Pub/Sub delivery + publisher retries are
at-least-once, so we dedupe IN-STREAM (DeduplicatePerKey on the producer-minted
idempotency_key, TTL --dedup_ttl_seconds); the Dataplex DQ scan + a reconciliation
MERGE are the detective/backstop controls. BigQuery's streaming insertId only
covers the sink's own transient retries, not business-level dedup.

Run modes:
  * Dataflow (streaming, Flex Template + custom container): --input_subscription
  * Local/offline (DirectRunner, batch):                    --input_file (JSON lines)

ADR-0003: packaged as a Flex Template, launched on-demand and drained by default.
"""
from __future__ import annotations

import argparse
import json
import logging
import os

import apache_beam as beam
from apache_beam.options.pipeline_options import (
    GoogleCloudOptions, PipelineOptions, StandardOptions)

from finchat_pipeline.dlp import MaybeDeidentify
from finchat_pipeline.enrich import enrich
from finchat_pipeline.schema import SILVER_SCHEMA
from finchat_pipeline.transforms import DLQ, VALID, ParseAndValidate


def build_pipeline(p, opts):
    if opts.input_file:
        raw = p | "ReadFile" >> beam.io.ReadFromText(opts.input_file)
    else:
        raw = p | "ReadPubSub" >> beam.io.ReadFromPubSub(subscription=opts.input_subscription)

    parsed = raw | "ParseValidate" >> beam.ParDo(ParseAndValidate()).with_outputs(DLQ, main=VALID)

    deduped = parsed[VALID]
    if int(getattr(opts, "dedup_ttl_seconds", 0) or 0) > 0:
        # Stateful dedup on the producer-minted business key, BEFORE DLP (don't pay
        # de-identification for duplicates). Keying introduces a shuffle — the price
        # of dedup-at-write; consumers stay simple and pay nothing.
        from apache_beam.transforms.deduplicate import DeduplicatePerKey
        from apache_beam.utils.timestamp import Duration
        deduped = (
            parsed[VALID]
            | "KeyByIdempotency" >> beam.Map(lambda r: (r["idempotency_key"], r))
            | "DedupRedeliveries" >> DeduplicatePerKey(
                processing_time_duration=Duration(seconds=int(opts.dedup_ttl_seconds)))
            | "DropDedupKey" >> beam.Values()
        )

    valid = (
        deduped
        | "Deidentify" >> beam.ParDo(MaybeDeidentify(
            opts.project, opts.deid_template, opts.inspect_template, opts.dlp_sample_rate))
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
    parser.add_argument("--inspect_template", default="")
    parser.add_argument("--dlp_sample_rate", type=float, default=0.1)
    parser.add_argument("--dedup_ttl_seconds", type=int, default=3600,
                        help="In-stream idempotency_key dedup window (0 disables).")
    parser.add_argument("--input_file")
    parser.add_argument("--output_file")
    parser.add_argument("--dlq_file")
    # Unknown args (e.g. --sdk_container_image, --runner, --temp_location) flow
    # straight into PipelineOptions — that is how the custom worker container is
    # selected at run time (--sdk_container_image=<image>).
    known, beam_args = parser.parse_known_args(argv)

    options = PipelineOptions(beam_args)
    # No save_main_session: the components are an installed package on the worker
    # image, so workers import them natively instead of unpickling __main__.
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
