"""Manual, date-specific changes to the classroom duty rotation."""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "duty_overrides.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_override(day: date) -> list[str] | None:
    pair = _load().get(day.isoformat())
    return pair if isinstance(pair, list) and len(pair) == 2 else None


def set_override(day: date, students: list[str]) -> None:
    data = _load()
    data[day.isoformat()] = students
    _save(data)


def clear_override(day: date) -> None:
    data = _load()
    if data.pop(day.isoformat(), None) is not None:
        _save(data)
