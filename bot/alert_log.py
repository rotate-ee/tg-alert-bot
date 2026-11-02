"""Журнал тривог: [{"start": iso, "end": iso|None, "level": "red"|"yellow"|None, "is_false": bool}]. Показується в міні-застосунку."""
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "alert_log.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
MAX_ENTRIES = 300


def _load() -> list:
    if not DATA_PATH.exists():
        return []
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
            # Гарантуємо наявність ключа is_false для старих записів
            for entry in data:
                if "is_false" not in entry:
                    entry["is_false"] = False
            return data
    except (OSError, ValueError):
        return []


def _save(data: list) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data[-MAX_ENTRIES:], f, ensure_ascii=False)


def _now() -> str:
    return datetime.now(KYIV_TZ).isoformat(timespec="seconds")


def _merge_level(old: str | None, new: str | None) -> str | None:
    return "red" if "red" in (old, new) else (new or old)


def on_started(level: str | None) -> None:
    data = _load()
    if data and data[-1]["end"] is None:  # рестарт бота посеред тривоги - не дублюємо
        data[-1]["level"] = _merge_level(data[-1]["level"], level)
    else:
        data.append({"start": _now(), "end": None, "level": level, "is_false": False})
    _save(data)


def on_level_changed(level: str | None) -> None:
    data = _load()
    if data and data[-1]["end"] is None:
        data[-1]["level"] = _merge_level(data[-1]["level"], level)
        _save(data)


def on_ended() -> None:
    data = _load()
    if data and data[-1]["end"] is None:
        data[-1]["end"] = _now()
        _save(data)


def recent(n: int = 20) -> list:
    # Повертаємо також індекс оригінального елемента у повному списку,
    # щоб фронтенд міг передати його назад для редагування/видалення.
    data = _load()
    indexed_data = [{"index": i, **item} for i, item in enumerate(data)]
    return indexed_data[-n:][::-1]


# --- Нові функції управління журналом ---

def update_entry(index: int, start: str | None = None, end: str | None = None, level: str | None = None, is_false: bool | None = None) -> bool:
    """Редагує запис за його індексом у повному списку."""
    data = _load()
    if 0 <= index < len(data):
        if start is not None:
            data[index]["start"] = start
        if end is not None:
            data[index]["end"] = end
        if level is not None:
            data[index]["level"] = level
        if is_false is not None:
            data[index]["is_false"] = is_false
        _save(data)
        return True
    return False


def delete_entry(index: int) -> bool:
    """Видаляє запис із журналу за індексом."""
    data = _load()
    if 0 <= index < len(data):
        data.pop(index)
        _save(data)
        return True
    return False


def toggle_false_flag(index: int) -> bool:
    """Змінює статус 'ложна тривога' на протилежний."""
    data = _load()
    if 0 <= index < len(data):
        data[index]["is_false"] = not data[index].get("is_false", False)
        _save(data)
        return True
    return False
