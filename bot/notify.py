"""
Особисті сповіщення в ЛС (підписки з міні-застосунку) і збір змін розкладу в одне повідомлення.

Теми підписки: "alerts" (початок/відбій/зміна рівня тривоги) і "changes" (зміни в розкладі).
Бот може написати користувачу лише після того, як той запустив бота (/start) - це
перевіряється при підписці пробним повідомленням.
"""
import asyncio
import json
import logging
import os
import time as _time
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

log = logging.getLogger("notify")
TOPICS = ("alerts", "changes")

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "subscribers.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def get_topics(user_id: int) -> list[str]:
    return list(_load().get(str(user_id), {}).get("topics", []))


def set_topics(user_id: int, topics: list[str], name: str = "") -> list[str]:
    clean = [t for t in TOPICS if t in topics]
    data = _load()
    if clean:
        data[str(user_id)] = {"topics": clean, "name": name[:60]}
    else:
        data.pop(str(user_id), None)
    _save(data)
    return clean


def subscribers(topic: str) -> list[int]:
    return [int(uid) for uid, v in _load().items() if topic in v.get("topics", [])]


async def send_to_subscribers(bot, topic: str, text: str, exclude: set[int] | None = None) -> int:
    """Розсилає HTML-повідомлення підписникам теми. Хто заблокував бота - автоматично відписується."""
    sent = 0
    for uid in subscribers(topic):
        if exclude and uid in exclude:
            continue
        for attempt in range(2):
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
                sent += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(min(e.retry_after, 5))
            except (TelegramForbiddenError, TelegramBadRequest):
                set_topics(uid, [])  # бот заблокований або чату не існує
                log.info("Підписника %s видалено: бот не може йому писати", uid)
                break
            except Exception:
                log.exception("Не вдалося надіслати особисте сповіщення %s", uid)
                break
        await asyncio.sleep(0.05)  # ліміт Telegram ~30 повідомлень/с
    return sent


# --- зміни розкладу: збираємо за дату і шлемо одним повідомленням після паузи ---

_pending: dict[str, dict] = {}


def record_change(date_iso: str, line: str, actor: int | None = None) -> None:
    item = _pending.setdefault(date_iso, {"lines": [], "last": 0.0, "actors": set()})
    if line not in item["lines"]:
        item["lines"].append(line)
    item["last"] = _time.time()
    if actor:
        item["actors"].add(actor)


def due_changes(quiet_seconds: float = 25.0) -> list[tuple[str, list[str], set[int]]]:
    """Дати, у яких адмін уже 'quiet_seconds' нічого не змінював: їх можна відправляти."""
    now, out = _time.time(), []
    for key in sorted(_pending):
        item = _pending[key]
        if now - item["last"] >= quiet_seconds:
            out.append((key, item["lines"], item["actors"]))
    for key, _, _ in out:
        _pending.pop(key, None)
    return out
