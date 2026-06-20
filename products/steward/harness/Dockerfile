FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY llm.py tools.py planner.py generator.py evaluator.py harness.py main.py ./
ENV PORT=8083
EXPOSE 8083
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
