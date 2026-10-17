"""Примусово дистанційні періоди (наприклад, тиждень після канікул): [{"id","from","to","title"}], дати включно. У ці дні is_school_week() = False."""
import json
import os
import uuid
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "remote_periods.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> list:
    if not DATA_PATH.exists():
        return []
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(data: list) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_all() -> list:
    return sorted(_load(), key=lambda h: h["from"])


def add(d_from: date, d_to: date, title: str) -> dict:
    item = {"id": uuid.uuid4().hex[:8], "from": d_from.isoformat(), "to": d_to.isoformat(), "title": title.strip() or "Дистанційне навчання"}
    data = _load()
    data.append(item)
    _save(data)
    return item


def remove(item_id: str) -> bool:
    data = _load()
    new = [h for h in data if h["id"] != item_id]
    if len(new) == len(data):
        return False
    _save(new)
    return True


def get_period(d: date) -> dict | None:
    iso = d.isoformat()
    for h in _load():
        if h["from"] <= iso <= h["to"]:
            return h
    return None
