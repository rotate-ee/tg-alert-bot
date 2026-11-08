"""Розсилка адмінів у ЛС бота (з журналом розсилок). Текст надсилається як є, без розмітки."""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import users

log = logging.getLogger("broadcast")
KYIV_TZ = ZoneInfo("Europe/Kyiv")
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
HISTORY_PATH = DATA_DIR / "broadcasts.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 20
SEND_PAUSE = 0.06  # ~16 повідомлень/с, з запасом під ліміт Telegram
_running = False


def is_running() -> bool:
    return _running


def try_begin() -> bool:
    global _running
    if _running:
        return False
    _running = True
    return True


def history(n: int = 5) -> list[dict]:
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)[-n:][::-1]
    except (OSError, ValueError):
        return []


def _log(entry: dict) -> None:
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = []
    data.append(entry)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(data[-MAX_HISTORY:], f, ensure_ascii=False)


async def _send_one(bot, uid: int, text: str, markup) -> str:
    for _ in range(2):
        try:
            await bot.send_message(uid, text, reply_markup=markup)
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 10))
        except (TelegramForbiddenError, TelegramBadRequest):
            return "blocked"
        except Exception:
            log.exception("Розсилка: не вдалося надіслати %s", uid)
            return "failed"
    return "failed"


async def run(bot, admin_id: int, text: str, audience: str = "all", button_url: str = "", test: bool = False) -> dict:
    """Розсилає текст. test=True - лише адміну. Недоступних (заблокували бота) позначає в реєстрі."""
    global _running
    try:
        recipients = [admin_id] if test else users.audience_ids(audience)
        markup = None
        if button_url:
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📅 Відкрити застосунок", web_app=WebAppInfo(url=button_url))]])
        result = {"sent": 0, "blocked": 0, "failed": 0}
        for uid in recipients:
            outcome = await _send_one(bot, uid, text, markup)
            result["sent" if outcome == "ok" else outcome] += 1
            if outcome == "blocked" and not test:
                users.set_blocked(uid, True)
            await asyncio.sleep(SEND_PAUSE)
        result["total"] = len(recipients)
        if not test:
            _log({"id": uuid.uuid4().hex[:8], "time": datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
                  "admin": admin_id, "text": text[:200], "audience": audience, **result})
            try:
                await bot.send_message(
                    admin_id, f"📢 Розсилку завершено\nДоставлено: {result['sent']} з {result['total']}\n"
                              f"Недоступні (заблокували бота): {result['blocked']}\nПомилки: {result['failed']}")
            except Exception:
                log.warning("Не вдалося надіслати звіт про розсилку адміну %s", admin_id)
        return result
    finally:
        _running = False
