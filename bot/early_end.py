"""
Зберігання відміток "урок закінчився раніше" (команда /endnow):
JSON-файл вида {"2026-09-07": {"3": "13:05"}} - урок номер 3 на цю
дату вважається завершеним о 13:05, а не в стандартний час.

На відміну від bell_overrides.py, це НЕ змінює сам розклад дзвінків
(в /today, /week, ранковій розсилці час і далі показується
стандартний) - позначка впливає тільки на пошук "поточного/наступного"
уроку (find_current_and_next у lessons.py).

Той самий DATA_DIR, що й у cancellations.py (спільний постійний диск).
"""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "early_end.json"
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


def mark_early_end(d: date, number: int, end_time: str) -> None:
    data = _load()
    k = _key(d)
    day = data.get(k, {})
    day[str(number)] = end_time
    data[k] = day
    _save(data)


def clear_early_end(d: date, number: int) -> bool:
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


def get_early_end(d: date, number: int) -> str | None:
    data = _load()
    return data.get(_key(d), {}).get(str(number))
