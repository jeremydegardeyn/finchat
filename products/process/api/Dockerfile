# Process API — cross-domain business capabilities (ADR-0030).
# Standard service-directory build context, like every other service here.
#
# The demo repositories are deliberately NOT copied. They exist so the service runs from
# a checkout with no GCP access; in a container TXN_API_URL and LOAN_API_URL are always
# set, so that path is unreachable and shipping the other services' modules would only
# create a way for them to drift.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py sources.py overview.py analyst_routing.py ./
ENV PORT=8090
EXPOSE 8090
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
