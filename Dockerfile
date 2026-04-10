FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY service_account.json .
ENV PORT=8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--worker-class", "uvicorn.workers.UvicornWorker", "--workers", "1", "main:app_flask"]
