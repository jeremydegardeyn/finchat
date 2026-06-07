# Dataflow Flex Template image (launcher). Workers run the stock Beam SDK image;
# the pipeline is a single self-contained module shipped via save_main_session,
# so no custom worker container is needed.
FROM gcr.io/dataflow-templates-base/python312-template-launcher-base:latest

ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/pipeline.py
ENV FLEX_TEMPLATE_PYTHON_REQUIREMENTS_FILE=/template/requirements.txt

WORKDIR /template
COPY requirements.txt .
COPY pipeline.py .
RUN pip install --no-cache-dir -r requirements.txt
