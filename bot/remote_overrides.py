"""
Зберігання підтверджень "7/8-й урок сьогодні очно": JSON-файл вида
{"2026-09-07": [7]} - номери уроків, які саме сьогодні пройдуть
очно, хоча за замовчуванням вважаються дистанційними.

Без запису для конкретної дати+номера урок лишається дистанційним
(значення за замовчуванням) - тобто відсутність запису НЕ означає
"підтверджено дистанційно", а просто "ще не підтверджено очно".

Той самий DATA_DIR, що й у cancellations.py (спільний постійний диск).
"""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "remote_overrides.json"
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


def mark_in_person(d: date, number: int) -> None:
    """Позначити, що саме сьогодні цей урок буде ОЧНО (підтверджено адміном)."""
    data = _load()
    k = _key(d)
    nums = set(data.get(k, []))
    nums.add(number)
    data[k] = sorted(nums)
    _save(data)


def is_in_person(d: date, number: int) -> bool:
    data = _load()
    return number in set(data.get(_key(d), []))


def clear_in_person(d: date, number: int) -> bool:
    """Прибрати підтвердження "очно" (урок знову дистанційний). True, якщо запис був."""
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
