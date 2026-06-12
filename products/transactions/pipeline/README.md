# Transactions Streaming Pipeline (Apache Beam / Dataflow)

`Pub/Sub → parse + schema enforcement → in-stream dedup → DLP de-identify (sampled) → enrich → BigQuery Silver`,
with malformed messages routed to a dead-letter queue. Packaged as a **Flex Template** with a
**custom container** and run **on-demand** by default (ADR-0003) to keep idle cost ~$0.

## Modular by design (custom worker container)

The pipeline is a pip-installable package, `finchat_pipeline/`, with one concern per module:

| Module | Concern | Beam? |
|---|---|---|
| `schema.py` | Silver sink schema + pipeline version | no |
| `validation.py` | parse / schema enforcement / DLQ envelope | no |
| `enrich.py` | lineage/provenance columns | no |
| `transforms.py` | Beam DoFn wrappers (`ParseAndValidate`, tagged outputs) | yes |
| `dlp.py` | sampled DLP de-identification DoFn | yes |
| `pipeline.py` | Beam graph wiring + Flex Template entrypoint (composition root) | yes |

The `Dockerfile` builds **one image used as both the Flex Template launcher and the Beam worker
SDK image**: it starts from `apache/beam_python3.12_sdk`, copies the Flex launcher entrypoint onto
it, and `pip install -e .` bakes the package in. Run with `--sdk_container_image=<that image>` and
workers import `finchat_pipeline.*` natively — **no `save_main_session`, no single-file inlining**.
(Lighter alternative if you don't want a custom image: `--setup_file ./setup.py`, which stages the
package onto stock workers at runtime.)

## Test offline (DirectRunner, no GCP)

```bash
# Pure components (no Beam needed):
pytest test_transforms.py                       # validation + enrich (10 cases)

# Full graph end-to-end, in the real worker artifact:
docker build -t txn-pipeline:local .
docker run --rm --entrypoint sh txn-pipeline:local -c '
  python -m finchat_pipeline.pipeline --input_file IN.jsonl \
    --output_file /tmp/out --dlq_file /tmp/dlq --dedup_ttl_seconds 3600 --dlp_sample_rate 0'

# Or, with Beam installed locally:
python -m finchat_pipeline.pipeline --input_file in.jsonl --output_file valid --dlq_file dlq
pytest test_dedup.py                            # DirectRunner dedup test
```

## Build the Flex Template (custom image)

```bash
REPO=us-central1-docker.pkg.dev/strongsville-city-schools/finchat-dev-images
BUCKET=finchat-dev-dataflow
docker build -t $REPO/txn-pipeline:latest .          # custom launcher+worker image
docker push $REPO/txn-pipeline:latest
gcloud dataflow flex-template build gs://$BUCKET/templates/txn-pipeline.json \
  --image $REPO/txn-pipeline:latest --sdk-language PYTHON --metadata-file metadata.json
```

(CI does this on every push; `scripts/run_dataflow.sh <env>` launches it and already passes
`sdk_container_image`.)

## Run on-demand (sandbox — drains after)

```bash
./../../../scripts/run_dataflow.sh dev            # launch (leave running)
./../../../scripts/run_dataflow.sh dev dlp 15     # + DLP, auto-drain after 15 min
```

The launch forwards `sdk_container_image=$REPO/txn-pipeline:latest` so the workers run the custom
image. **Enable DLP de-identification** with the `dlp` arg (adds the deid/inspect templates +
sample rate); omit it and `MaybeDeidentify` is a no-op.

For the **enterprise** 24/7 streaming job, set `enable_streaming_job = true` in Terraform — same
template, different lifetime.

## Design notes

- **Validation** lives in `validation.parse_and_validate` (Beam-free, unit-tested); `transforms.ParseAndValidate` is the tagged-output DoFn that routes valid → main, failures → DLQ with the error reason + raw payload.
- **In-stream dedup**: `DeduplicatePerKey` on the producer-minted `idempotency_key` (TTL `--dedup_ttl_seconds`), before DLP so duplicates don't incur de-identification cost. Pub/Sub delivery + publisher retries are at-least-once; the Dataplex DQ scan asserts key uniqueness at rest and a reconciliation `MERGE` is the backstop. BigQuery's streaming `insertId` only covers the sink's own transient retries — not business-level dedup.
- **DLP**: sampled de-identification (`--dlp_sample_rate`) keeps sandbox cost near zero.
- **Lineage**: `enrich` stamps `ingest_time`, `source_system`, `pipeline_version` on every row.
