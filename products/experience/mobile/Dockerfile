# Mobile Experience API — one screen, one round trip (ADR-0030).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py ./
ENV PORT=8091
EXPOSE 8091
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
