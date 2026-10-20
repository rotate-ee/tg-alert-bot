"""
Канікули: періоди дат, коли уроків немає. JSON у DATA_DIR:
[{"start": "2026-10-26", "end": "2026-11-01", "name": "Осінні канікули"}]

Необов'язкові поля після канікул:
  "after": "home" | "school"  - яким буде тиждень(і) одразу після канікул
  "after_weeks": N            - скільки таких тижнів поспіль (за замовчуванням 1);
                                далі чергування "школа/вдома" продовжується само.

При першому запуску (файлу ще немає) підтягуються канікули за замовчуванням
з holidays_default.json (орієнтовно: осінні 26.10-01.11.2026, після них
дистанційка). Далі все редагується з адмінки/команди /holiday.

У канікули lessons.get_today_lessons() повертає порожній список, тому всі
функції бота (розклад, нагадування, питання про відпускання...) самі
"вимикаються" без додаткових перевірок.
"""
import json
import os
from datetime import date
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "holidays.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


DEFAULTS_PATH = Path(__file__).parent / "holidays_default.json"


def _seed_defaults() -> list[dict]:
    """Перший запуск: початкові канікули з файлу за замовчуванням (якщо не вимкнено HOLIDAYS_SEED=0)."""
    if os.getenv("HOLIDAYS_SEED", "1") == "0" or not DEFAULTS_PATH.exists():
        return []
    try:
        with open(DEFAULTS_PATH, encoding="utf-8") as f:
            items = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    _save(items)
    return items


def _load() -> list[dict]:
    if not DATA_PATH.exists():
        return _seed_defaults()
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict]) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def list_all() -> list[dict]:
    return sorted(_load(), key=lambda h: h["start"])


def add(start: date, end: date, name: str = "", after: str | None = None, after_weeks: int = 1) -> None:
    """Додає канікули (якщо з такою ж датою початку вже є - замінює)."""
    if end < start:
        raise ValueError("Кінець канікул раніше за початок")
    if after not in (None, "home", "school"):
        raise ValueError("after має бути home або school")
    if not 1 <= int(after_weeks) <= 8:
        raise ValueError("Кількість тижнів після канікул: від 1 до 8")
    name = (name or "").strip()[:60] or "Канікули"
    items = [h for h in _load() if h["start"] != start.isoformat()]
    item = {"start": start.isoformat(), "end": end.isoformat(), "name": name}
    if after:
        item["after"] = after
        item["after_weeks"] = int(after_weeks)
    items.append(item)
    _save(items)


def remove(start: date) -> bool:
    items = _load()
    kept = [h for h in items if h["start"] != start.isoformat()]
    if len(kept) == len(items):
        return False
    _save(kept)
    return True


def holiday_on(d: date) -> dict | None:
    iso = d.isoformat()
    for h in list_all():
        if h["start"] <= iso <= h["end"]:
            return h
    return None


def upcoming(d: date, limit: int = 6) -> list[dict]:
    """Поточні та майбутні канікули (ті, що ще не закінчились)."""
    return [h for h in list_all() if h["end"] >= d.isoformat()][:limit]
