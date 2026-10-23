"""
Хранение отменённых учителями уроков: JSON-файл вида
{"2026-09-07": [3, 5]} - номера уроков, отменённых на конкретную дату.

Путь к файлу можно задать переменной окружения DATA_DIR (например,
примонтированный Railway Volume вроде /data) - иначе используется
локальная папка bot/data рядом с кодом. Без постоянного диска на
Railway этот файл пропадает при каждом передеплое/рестарте контейнера.
"""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "cancellations.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


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


def _day_data(data: dict, d: date) -> tuple[set[int], dict[int, set[str]]]:
    value = data.get(_key(d), [])
    # Older files stored a bare list of lesson numbers.
    if isinstance(value, list):
        return set(value), {}
    numbers = set(value.get("numbers", []))
    groups = {int(number): set(names) for number, names in value.get("groups", {}).items()}
    return numbers, groups


def get_cancelled_numbers(d: date) -> set[int]:
    return _day_data(_load(), d)[0]


def get_cancelled_groups(d: date) -> dict[int, set[str]]:
    return _day_data(_load(), d)[1]


def cancel_lesson(d: date, number: int) -> None:
    data = _load()
    k = _key(d)
    nums, groups = _day_data(data, d)
    nums.add(number)
    data[k] = {"numbers": sorted(nums), "groups": {str(n): sorted(v) for n, v in groups.items() if v}}
    _save(data)


def uncancel_lesson(d: date, number: int) -> bool:
    """Возвращает True, если урок был отменён и теперь снова активен."""
    data = _load()
    k = _key(d)
    nums, groups = _day_data(data, d)
    if number not in nums:
        return False
    nums.discard(number)
    if nums or groups:
        data[k] = {"numbers": sorted(nums), "groups": {str(n): sorted(v) for n, v in groups.items() if v}}
    else:
        data.pop(k, None)
    _save(data)
    return True


def set_group_cancelled(d: date, number: int, group: str, cancelled: bool) -> None:
    if group not in {"girls", "boys"}:
        raise ValueError("group")
    data = _load()
    k = _key(d)
    numbers, groups = _day_data(data, d)
    selected = groups.setdefault(number, set())
    if cancelled:
        selected.add(group)
    else:
        selected.discard(group)
    if not selected:
        groups.pop(number, None)
    if numbers or groups:
        data[k] = {"numbers": sorted(numbers), "groups": {str(n): sorted(v) for n, v in groups.items() if v}}
    else:
        data.pop(k, None)
    _save(data)


def diagnostics() -> str:
    """
    Людський опис того, куди саме зараз пишуться дані, і чи реально
    працює запис/читання прямо зараз - для перевірки без доступу до
    хостингу (наприклад, з телефону).
    """
    lines = [
        f"DATA_DIR (env): {_data_dir or '(не задано, використовую локальну bot/data)'}",
        f"Шлях до файлу: {DATA_PATH}",
        f"Папка існує: {'так' if DATA_PATH.parent.exists() else 'ні'}",
        f"Файл існує: {'так' if DATA_PATH.exists() else 'ні'}",
    ]

    if DATA_PATH.exists():
        try:
            size = DATA_PATH.stat().st_size
            lines.append(f"Розмір файлу: {size} байт")
            lines.append(f"Вміст: {DATA_PATH.read_text(encoding='utf-8')[:500]}")
        except Exception as e:
            lines.append(f"Не вдалося прочитати файл: {e}")

    # тест запису/читання прямо зараз
    test_key = "__diag_test__"
    try:
        data = _load()
        data[test_key] = [datetime_now_marker()]
        _save(data)
        reread = _load()
        ok = test_key in reread
        data.pop(test_key, None)
        _save(data)
        lines.append(f"Тестовий запис+читання: {'✅ працює' if ok else '❌ не працює'}")
    except Exception as e:
        lines.append(f"Тестовий запис+читання: ❌ помилка ({e})")

    return "\n".join(lines)


def datetime_now_marker() -> str:
    from datetime import datetime as _dt
    return _dt.now().isoformat()
