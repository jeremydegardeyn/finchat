# FinChat MCP server — streamable-HTTP transport for Cloud Run (ADR-0027).
# Build context is the REPO ROOT, not this directory: the server reuses modules
# owned by other services, and copying them is what keeps the knowledge plane a
# single source of truth instead of a snapshot.
#
#   docker build -f mcp_server/Dockerfile -t finchat-mcp .
FROM python:3.12-slim

WORKDIR /app

COPY mcp_server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The server itself.
COPY mcp_server/server.py mcp_server/backends.py mcp_server/knowledge.py \
     mcp_server/loader.py ./mcp_server/

# Modules reused from the services that own them. Every one of these is asserted
# by test_mcp_image.py — a missing COPY here fails at first tool call, not at
# build, which is the worst place to find out.
COPY ui/_okf_context.py ./ui/
COPY products/transactions/api/bq.py ./products/transactions/api/
COPY products/transactions/agent/retrieval.py ./products/transactions/agent/
# 8KB. Ships so the KB tool degrades to local BM25 instead of going dark when the
# agent service is unreachable or predates its /search endpoint.
COPY products/transactions/agent/kb/corpus.jsonl ./products/transactions/agent/kb/
COPY products/loans/api/store.py products/loans/api/risk.py ./products/loans/api/
COPY knowledge/ontology.yaml ./knowledge/
COPY contracts/ ./contracts/

ENV FINCHAT_MCP_TRANSPORT=http \
    PORT=8080

CMD ["python", "mcp_server/server.py"]
