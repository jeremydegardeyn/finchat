# Custom container used as BOTH the Flex Template launcher AND the Dataflow
# worker harness, so our own modules (transforms.py) are importable on workers.
# (Default Flex Templates run workers on the stock Beam SDK image, which would
#  not contain transforms.py -> ModuleNotFoundError.)
FROM apache/beam_python3.12_sdk:2.58.0

# Bring in the Flex Template launcher entrypoint from the templates base image.
COPY --from=gcr.io/dataflow-templates-base/python312-template-launcher-base \
     /opt/google/dataflow/python_template_launcher \
     /opt/google/dataflow/python_template_launcher

WORKDIR /template
COPY requirements.txt pipeline.py transforms.py ./
RUN pip install --no-cache-dir -r requirements.txt

ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/pipeline.py
ENV FLEX_TEMPLATE_PYTHON_REQUIREMENTS_FILE=/template/requirements.txt
# Make /template importable on launcher AND worker (so `import transforms` works).
ENV PYTHONPATH=/template

ENTRYPOINT ["/opt/google/dataflow/python_template_launcher"]
