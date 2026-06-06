FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY risk.py store.py main.py ./
ENV PORT=8081
EXPOSE 8081
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
