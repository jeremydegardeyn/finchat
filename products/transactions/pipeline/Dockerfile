# Custom Dataflow container — serves as BOTH the Flex Template launcher AND the
# Beam worker SDK image. Run the template with --sdk_container_image=<this image>
# so workers use it too; then the pipeline is an INSTALLED PACKAGE on the worker
# and the modular components (validation / transforms / dlp / enrich) import
# natively — no save_main_session, no single-file inlining.
#
# Pattern: start from the Beam SDK worker base (it carries the SDK + worker boot),
# then copy the Flex Template launcher entrypoint onto it from the template base.

FROM gcr.io/dataflow-templates-base/python312-template-launcher-base:latest AS launcher

FROM apache/beam_python3.12_sdk:2.58.0

# Flex Template launcher entrypoint, lifted from the template base.
COPY --from=launcher /opt/google/dataflow/python_template_launcher /opt/google/dataflow/python_template_launcher

ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/finchat_pipeline/pipeline.py
ENV FLEX_TEMPLATE_PYTHON_SETUP_FILE=/template/setup.py

WORKDIR /template
COPY . /template
# apache-beam[gcp] is already in the base image; this adds the package itself
# (importable as finchat_pipeline) plus google-cloud-dlp.
RUN pip install --no-cache-dir -e .

ENTRYPOINT ["/opt/google/dataflow/python_template_launcher"]
