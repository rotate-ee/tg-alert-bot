"""
Зберігання одноразових "додаткових" уроків на конкретну дату - тих,
яких немає в стандартному schedule.json (наприклад, 0-й урок).
JSON-файл вида:
{"2026-09-07": [{"number": 0, "name": "Історія", "start": "10:35", "end": "11:20"}]}

Той самий DATA_DIR, що й у cancellations.py (спільний постійний диск).
"""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "extra_lessons.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _key(d: date) -> str:
    return d.isoformat()


def add_extra_lesson(d: date, number: int, name: str, start: str, end: str) -> None:
    """Додає (або замінює, якщо номер вже був) додатковий урок на цю дату."""
    data = _load()
    k = _key(d)
    day = [l for l in data.get(k, []) if l["number"] != number]
    day.append({"number": number, "name": name, "start": start, "end": end})
    data[k] = day
    _save(data)


def remove_extra_lesson(d: date, number: int) -> bool:
    data = _load()
    k = _key(d)
    day = data.get(k, [])
    new_day = [l for l in day if l["number"] != number]
    if len(new_day) == len(day):
        return False
    if new_day:
        data[k] = new_day
    else:
        data.pop(k, None)
    _save(data)
    return True


def get_extra_lessons(d: date) -> list[dict]:
    data = _load()
    return data.get(_key(d), [])
