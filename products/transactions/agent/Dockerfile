# Cloud Run image for the Banking Assistant (ADK + Gemini via Vertex).
# Scale-to-zero, ~$0 idle (ADR-0004). Runtime SA needs roles/aiplatform.user.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# NOTE: this COPY names every module explicitly — a new file that is not listed here is
# simply absent at runtime and fails to import. retrieval.py was missed exactly that way,
# and then trajectory.py was missed by someone reading this note, so the list is now held
# by scripts/test_image_modules.py instead: it walks the imports and fails red rather than
# at deploy time. Keep adding names here; the test is what checks you did.
COPY agent.py tools.py server.py retrieval.py gateway_llm.py tracing.py trajectory.py ./
# Use Vertex AI as the Gemini backend (no API key; uses the runtime SA).
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV PORT=8083
EXPOSE 8083
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
