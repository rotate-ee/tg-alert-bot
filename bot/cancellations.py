"""
Хранение отменённых учителями уроков: JSON-файл вида
{"2026-09-07": [3, 5]} - номера уроков, отменённых на конкретную дату.
"""
import json
from datetime import date
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "cancellations.json"
DATA_PATH.parent.mkdir(exist_ok=True)


def _load() -> dict:
    if not DATA_PATH.exists():
        return {}
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _key(d: date) -> str:
    return d.isoformat()


def get_cancelled_numbers(d: date) -> set[int]:
    data = _load()
    return set(data.get(_key(d), []))


def cancel_lesson(d: date, number: int) -> None:
    data = _load()
    k = _key(d)
    nums = set(data.get(k, []))
    nums.add(number)
    data[k] = sorted(nums)
    _save(data)


def uncancel_lesson(d: date, number: int) -> bool:
    """Возвращает True, если урок был отменён и теперь снова активен."""
    data = _load()
    k = _key(d)
    nums = set(data.get(k, []))
    if number not in nums:
        return False
    nums.discard(number)
    if nums:
        data[k] = sorted(nums)
    else:
        data.pop(k, None)
    _save(data)
    return True
