FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# NOTE: this COPY names every module explicitly — a new file that is not listed here is
# simply absent at runtime and fails to import. gateway_client.py and user_tokens.py were
# both missed exactly that way; test_ui_image.py now guards it.
COPY server.py armor.py _okf_context.py gateway_client.py user_tokens.py index.html eval_report.json ./
ENV PORT=8082
EXPOSE 8082
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
