FROM python:3.13-slim

WORKDIR /app

ENV PERSIST_DIR=/app/persist

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

CMD ["python", "main.py"]
