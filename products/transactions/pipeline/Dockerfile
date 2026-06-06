# Dataflow Flex Template image for the transactions streaming pipeline.
FROM gcr.io/dataflow-templates-base/python312-template-launcher-base:latest

ENV FLEX_TEMPLATE_PYTHON_PY_FILE=/template/pipeline.py
ENV FLEX_TEMPLATE_PYTHON_REQUIREMENTS_FILE=/template/requirements.txt

WORKDIR /template
COPY requirements.txt .
COPY pipeline.py .
COPY transforms.py .
RUN pip install --no-cache-dir -r requirements.txt
