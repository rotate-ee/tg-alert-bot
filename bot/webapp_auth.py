"""
Перевірка того, що запит до API прийшов з Mini App від справжнього
користувача Telegram, а не від сторонньої людини.

Telegram передає в застосунок рядок initData, підписаний токеном бота.
Ми перевіряємо підпис (бібліотекою aiogram) і свіжість auth_date.
Підробити такий підпис без токена бота неможливо.
"""
import logging
from datetime import datetime, timezone

from aiogram.utils.web_app import safe_parse_webapp_init_data

log = logging.getLogger("webapp.auth")

MAX_AGE_SECONDS = 24 * 3600  # initData старше доби вважаємо простроченим


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age: int = MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict | None:
    """Повертає {"id", "first_name", "username"} або None, якщо підпис/термін невірні."""
    if not init_data:
        return None
    try:
        parsed = safe_parse_webapp_init_data(token=bot_token, init_data=init_data)
    except Exception as e:  # невірний підпис, зіпсований рядок тощо
        log.info("initData відхилено: %s", type(e).__name__)
        return None

    if parsed.user is None:
        return None

    now = now or datetime.now(timezone.utc)
    auth_date = parsed.auth_date
    if auth_date.tzinfo is None:
        auth_date = auth_date.replace(tzinfo=timezone.utc)
    age = (now - auth_date).total_seconds()
    if age > max_age or age < -300:  # прострочено або "з майбутнього"
        return None

    return {
        "id": parsed.user.id,
        "first_name": parsed.user.first_name,
        "username": parsed.user.username,
    }
