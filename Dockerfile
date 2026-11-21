FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
RUN adduser --system --uid 10001 --group appuser \
    && mkdir -p /app/bot/data \
    && chown -R appuser:appuser /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Токены НЕ копируются в образ - они задаются через переменные
# окружения на самом хостинге (см. README, раздел "Деплой").
CMD ["python", "bot/main.py"]
