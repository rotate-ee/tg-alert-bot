FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# Токены НЕ копируются в образ - они задаются через переменные
# окружения на самом хостинге (см. README, раздел "Деплой").
CMD ["python", "bot/main.py"]
