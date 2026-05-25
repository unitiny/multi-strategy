FROM python:3.13-slim

WORKDIR /app

ENV PERSIST_DIR=/app/persist
ARG GIT_SHA=unknown
ENV APP_GIT_SHA=$GIT_SHA

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN test ! -e /app/data \
    && python -c "from core.engine import Engine; import dateparser; print('import check ok')"

CMD ["python", "main.py"]
