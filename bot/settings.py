"""
Налаштування бота, які адмін міняє "на льоту" (з Mini App), без заходу
на хостинг і без перезапуску. Значення за замовчуванням беруться з config
(змінні середовища); тут зберігаються тільки ЗМІНЕНІ адміном значення.
"""
import json
import os
import re
from datetime import date
from pathlib import Path

import config

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "settings.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
BOOL_KEYS = ("notify_start", "notify_ended", "show_threats", "ask_dismiss")
TIME_KEYS = ("morning_digest_time", "remote_confirm_time", "weektype_time")


def _norm_time(raw) -> str | None:
    s = str(raw if raw is not None else "").strip().lower()
    if s in ("", "off", "none", "null"):
        return None
    m = _TIME.match(s)
    if not m:
        raise ValueError("Час має бути у форматі ЧЧ:ММ")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _safe_time(raw) -> str | None:
    try:
        return _norm_time(raw)
    except ValueError:
        return None


def defaults() -> dict:
    return {
        "notify_start": bool(config.NOTIFY_ON_START),   # повідомлення про початок тривоги / зміну рівня
        "notify_ended": True,                            # повідомлення про відбій
        "show_threats": True,                            # рядок "Загрози: дрони, балістика..."
        "ask_dismiss": True,                             # питання адміну "Вас відпустили?"
        "reminder_minutes": config.LESSON_REMINDER_MINUTES,  # нагадування за N хв до уроку (None - вимк.)
        "morning_digest_time": _safe_time(config.MORNING_DIGEST_TIME),
        "remote_confirm_time": _safe_time(config.REMOTE_CONFIRM_TIME),
        "weektype_time": _safe_time(config.WEEK_TYPE_REMINDER_TIME),
        "school_week_anchor": config.SCHOOL_WEEK_ANCHOR_MONDAY or "",
    }


def _load_overrides() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _validate(key: str, value):
    if key in BOOL_KEYS:
        if not isinstance(value, bool):
            raise ValueError(f"{key}: очікується true/false")
        return value
    if key == "reminder_minutes":
        if value in (None, "", 0, "off"):
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError("Хвилини мають бути числом")
        if not 1 <= n <= 120:
            raise ValueError("Нагадування: від 1 до 120 хвилин")
        return n
    if key in TIME_KEYS:
        return _norm_time(value)
    if key == "school_week_anchor":
        if not value:
            return ""
        try:
            date.fromisoformat(str(value))
        except ValueError:
            raise ValueError("Дата має бути у форматі РРРР-ММ-ДД")
        return str(value)
    raise ValueError(f"Невідомий параметр: {key}")


def all() -> dict:
    merged = defaults()
    for k, v in _load_overrides().items():
        if k in merged:
            merged[k] = v
    return merged


def get(key: str):
    return all()[key]


def set_many(changes: dict) -> dict:
    """Перевіряє ВСІ зміни, і тільки якщо все гаразд - зберігає (атомарно)."""
    clean = {k: _validate(k, v) for k, v in changes.items()}
    overrides = _load_overrides()
    overrides.update(clean)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)
    return all()
