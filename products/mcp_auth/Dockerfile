# FinChat MCP OAuth proxy (ADR-0020).
#
# Build context is THIS directory, like every other service here — it shares no modules
# with the rest of the repo, deliberately. An authorization server that imports the
# platform's helpers acquires the platform's blast radius, and this is the one service
# that is reachable from the public internet by design.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py oauth.py ./

ENV PORT=8080
CMD ["python", "main.py"]
