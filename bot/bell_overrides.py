"""
Зберігання разових змін часу дзвінка для конкретного уроку на
конкретну дату: JSON-файл вида
{"2026-09-07": {"3": {"start": "12:50", "end": "13:20"}}}

Такий запис перекриває стандартний час з bells у schedule.json тільки
для цього уроку і тільки на цю дату - розклад інших днів не міняється.

Той самий DATA_DIR, що й у cancellations.py (спільний постійний диск).
"""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "bell_overrides.json"
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


def set_override(d: date, number: int, start: str, end: str) -> None:
    data = _load()
    k = _key(d)
    day = data.get(k, {})
    day[str(number)] = {"start": start, "end": end}
    data[k] = day
    _save(data)


def clear_override(d: date, number: int) -> bool:
    """Повертає True, якщо для цього уроку/дати дійсно був запис і його прибрали."""
    data = _load()
    k = _key(d)
    day = data.get(k, {})
    if str(number) not in day:
        return False
    day.pop(str(number))
    if day:
        data[k] = day
    else:
        data.pop(k, None)
    _save(data)
    return True


def get_override(d: date, number: int) -> dict | None:
    data = _load()
    return data.get(_key(d), {}).get(str(number))
