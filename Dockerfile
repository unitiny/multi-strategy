FROM python:3.13-slim

WORKDIR /app

ENV PERSIST_DIR=/app/persist

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
