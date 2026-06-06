# Cloud Run Job image for the synthetic transaction generator.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY generate.py .
# Parameters supplied via Cloud Run Job args / env at execution time.
ENTRYPOINT ["python", "generate.py"]
