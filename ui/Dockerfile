FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py armor.py index.html eval_report.json ./
ENV PORT=8082
EXPOSE 8082
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
