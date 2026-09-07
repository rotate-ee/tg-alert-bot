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
