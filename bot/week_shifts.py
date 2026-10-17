"""
Зсуви чергування очних/дистанційних тижнів: [{"id","from","school"}].
"from" - понеділок, з якого тиждень має тип school (True - очний, False - вдома),
далі тижні чергуються. Діє до наступного зсуву. Базове чергування - SCHOOL_WEEK_ANCHOR_MONDAY.

Якщо файлу ще немає, він створюється з одним зсувом: після осінніх канікул 2026
(24.10-01.11) тиждень з 02.11 дистанційний, далі очний (09.11) і так чергуються.
"""
import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "week_shifts.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
SEED = [{"id": "autumn26", "from": "2026-11-02", "school": False}]


def _load() -> list:
    if not DATA_PATH.exists():
        _save(SEED)
        return list(SEED)
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(data: list) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_all() -> list:
    return sorted(_load(), key=lambda s: s["from"])


def add(d: date, school: bool) -> dict:
    monday = d - timedelta(days=d.weekday())
    data = [s for s in _load() if s["from"] != monday.isoformat()]  # один зсув на тиждень
    item = {"id": uuid.uuid4().hex[:8], "from": monday.isoformat(), "school": bool(school)}
    data.append(item)
    _save(data)
    return item


def remove(item_id: str) -> bool:
    data = _load()
    new = [s for s in data if s["id"] != item_id]
    if len(new) == len(data):
        return False
    _save(new)
    return True


def resolve(monday: date, anchor: date) -> bool:
    """True - очний тиждень для цього понеділка з урахуванням усіх зсувів."""
    base_date, base_school = anchor, True
    for s in list_all():
        if s["from"] <= monday.isoformat():
            base_date, base_school = date.fromisoformat(s["from"]), s["school"]
    diff = (monday - base_date).days // 7
    return (diff % 2 == 0) == base_school
