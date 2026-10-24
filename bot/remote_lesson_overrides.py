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


def _day_data(value: object) -> tuple[set[int], dict[int, set[str]]]:
    # Older files stored a bare list of lesson numbers.
    if isinstance(value, list):
        return set(value), {}
    if not isinstance(value, dict):
        return set(), {}
    numbers = set(value.get("numbers", []))
    groups = {int(n): set(names) for n, names in value.get("groups", {}).items()}
    return numbers, groups


def _store_day(data: dict, key: str, numbers: set[int], groups: dict[int, set[str]]) -> None:
    groups = {n: names for n, names in groups.items() if names}
    if groups:
        data[key] = {
            "numbers": sorted(numbers),
            "groups": {str(n): sorted(names) for n, names in groups.items()},
        }
    elif numbers:
        # Keep the legacy compact format for whole-lesson overrides.
        data[key] = sorted(numbers)
    else:
        data.pop(key, None)


def is_remote(d: date, number: int, subgroup: str | None = None) -> bool:
    numbers, groups = _day_data(_load().get(_key(d), []))
    return number in numbers or (subgroup is not None and subgroup in groups.get(number, set()))


def is_group_remote(d: date, number: int, subgroup: str) -> bool:
    _, groups = _day_data(_load().get(_key(d), []))
    return subgroup in groups.get(number, set())


def set_remote(d: date, number: int, subgroup: str | None = None) -> None:
    data = _load()
    key = _key(d)
    numbers, groups = _day_data(data.get(key, []))
    if subgroup is None:
        numbers.add(number)
    else:
        if subgroup not in {"girls", "boys"}:
            raise ValueError("subgroup")
        groups.setdefault(number, set()).add(subgroup)
    _store_day(data, key, numbers, groups)
    _save(data)


def clear_remote(d: date, number: int, subgroup: str | None = None) -> bool:
    data = _load()
    key = _key(d)
    numbers, groups = _day_data(data.get(key, []))
    if subgroup is None:
        if number not in numbers:
            return False
        numbers.discard(number)
    else:
        selected = groups.get(number, set())
        if subgroup not in selected:
            return False
        selected.discard(subgroup)
        if selected:
            groups[number] = selected
        else:
            groups.pop(number, None)
    _store_day(data, key, numbers, groups)
    _save(data)
    return True
