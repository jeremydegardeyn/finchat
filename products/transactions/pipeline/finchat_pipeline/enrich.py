"""Enrichment component — lineage/provenance columns required by the Silver schema.

Beam-free pure function; the pipeline applies it with beam.Map.
"""
from __future__ import annotations

from datetime import datetime, timezone

from finchat_pipeline.schema import PIPELINE_VERSION


def enrich(record: dict, source_system: str = "synthetic-generator") -> dict:
    """Add ingest_time / source_system / pipeline_version provenance columns."""
    record = dict(record)
    record["ingest_time"] = datetime.now(timezone.utc).isoformat()
    record["source_system"] = source_system
    record["pipeline_version"] = PIPELINE_VERSION
    return record
