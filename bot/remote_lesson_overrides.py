"""Persist per-date admin overrides that force an individual lesson online."""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "remote_lesson_overrides.json"
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


def is_remote(d: date, number: int) -> bool:
    return number in set(_load().get(_key(d), []))


def set_remote(d: date, number: int) -> None:
    data = _load()
    numbers = set(data.get(_key(d), []))
    numbers.add(number)
    data[_key(d)] = sorted(numbers)
    _save(data)


def clear_remote(d: date, number: int) -> bool:
    data = _load()
    key = _key(d)
    numbers = set(data.get(key, []))
    if number not in numbers:
        return False
    numbers.discard(number)
    if numbers:
        data[key] = sorted(numbers)
    else:
        data.pop(key, None)
    _save(data)
    return True
