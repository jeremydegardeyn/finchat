# Process API — cross-domain business capabilities (ADR-0030).
# Build context is the REPO ROOT: the demo fallback reuses the two system APIs' own
# sample repositories, so the service runs without GCP.
#   docker build -f products/process/api/Dockerfile -t finchat-process .
FROM python:3.12-slim
WORKDIR /app
COPY products/process/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY products/process/api/main.py products/process/api/backends.py ./
# Demo-mode repositories, owned by the services they belong to.
COPY products/transactions/api/bq.py ./products/transactions/api/
COPY products/loans/api/store.py ./products/loans/api/
ENV PORT=8090
EXPOSE 8090
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
