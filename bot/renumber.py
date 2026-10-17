"""Разова зміна номерів уроків на дату: {"2026-10-19": {"3": 5, "5": 3}} - ключ це номер за розкладом, значення - новий номер (слот дзвінка)."""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "renumber.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_map(d: date) -> dict[int, int]:
    return {int(k): int(v) for k, v in _load().get(d.isoformat(), {}).items()}


def set_map(d: date, mapping: dict[int, int]) -> None:
    data = _load()
    if mapping:
        data[d.isoformat()] = {str(k): v for k, v in mapping.items()}
    else:
        data.pop(d.isoformat(), None)
    _save(data)


def clear(d: date) -> None:
    set_map(d, {})
