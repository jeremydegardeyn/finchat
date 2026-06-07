# Cloud Run image for the Banking Assistant (ADK + Gemini via Vertex).
# Scale-to-zero, ~$0 idle (ADR-0004). Runtime SA needs roles/aiplatform.user.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py tools.py server.py ./
# Use Vertex AI as the Gemini backend (no API key; uses the runtime SA).
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV PORT=8083
EXPOSE 8083
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
