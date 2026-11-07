"""Журнал тривог: [{"start": iso, "end": iso|None, "level": "red"|"yellow"|None, "is_false": bool, "id": str}]. Показується в міні-застосунку."""
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "alert_log.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
MAX_ENTRIES = 3000
LEVELS = ("red", "yellow")
_KEEP = object()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _norm_ts(value) -> str:
    """"2026-11-02 19:05" / ISO з поясом або без -> ISO з київським поясом (секунди). ValueError, якщо дата некоректна."""
    dt = datetime.fromisoformat(str(value).strip())
    dt = dt.replace(tzinfo=KYIV_TZ) if dt.tzinfo is None else dt.astimezone(KYIV_TZ)
    return dt.isoformat(timespec="seconds")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _order(data: list) -> list:
    """Хронологічно; незакрита (поточна) тривога завжди остання - на неї спираються on_ended/on_level_changed."""
    return sorted(data, key=lambda e: (e["end"] is None, _dt(e["start"])))


def _load() -> list:
    if not DATA_PATH.exists():
        return []
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    changed = False
    for e in data:  # міграція старих записів і лікування "наївних" дат
        if "is_false" not in e:
            e["is_false"], changed = False, True
        if not e.get("id"):
            e["id"], changed = _new_id(), True
        for k in ("start", "end"):
            if e.get(k):
                try:
                    n = _norm_ts(e[k])
                except ValueError:
                    continue
                if n != e[k]:
                    e[k], changed = n, True
    if changed:
        _save(data)
    return data


def _save(data: list) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data[-MAX_ENTRIES:], f, ensure_ascii=False)


def _now() -> str:
    return datetime.now(KYIV_TZ).isoformat(timespec="seconds")


def _merge_level(old: str | None, new: str | None) -> str | None:
    return "red" if "red" in (old, new) else (new or old)


def on_started(level: str | None) -> None:
    data = _load()
    if data and data[-1]["end"] is None:  # рестарт бота посеред тривоги - не дублюємо
        data[-1]["level"] = _merge_level(data[-1]["level"], level)
    else:
        data.append({"id": _new_id(), "start": _now(), "end": None, "level": level, "is_false": False})
    _save(data)


def on_level_changed(level: str | None) -> None:
    data = _load()
    if data and data[-1]["end"] is None:
        data[-1]["level"] = _merge_level(data[-1]["level"], level)
        _save(data)


def on_ended() -> None:
    data = _load()
    if data and data[-1]["end"] is None:
        data[-1]["end"] = _now()
        _save(data)


def all_entries() -> list:
    return _load()


def recent(n: int = 20) -> list:
    return _load()[-n:][::-1]


# --- Редагування журналу (адмін, за стабільним id) ---

def get(entry_id: str) -> dict | None:
    return next((e for e in _load() if e["id"] == entry_id), None)


def _check(entry: dict) -> None:
    if entry["level"] not in LEVELS:
        raise ValueError("level")
    start = _dt(entry["start"])
    if start > datetime.now(KYIV_TZ) + timedelta(minutes=5):
        raise ValueError("future")
    if entry["end"] and _dt(entry["end"]) < start:
        raise ValueError("range")


def update(entry_id: str, *, start=None, end=_KEEP, level=None, is_false=None) -> dict:
    data = _load()
    entry = next((e for e in data if e["id"] == entry_id), None)
    if entry is None:
        raise KeyError("id")
    new = dict(entry)
    if start:
        new["start"] = _norm_ts(start)
    if end is not _KEEP:
        new["end"] = _norm_ts(end) if end else None
    if level is not None:
        new["level"] = level
    if is_false is not None:
        new["is_false"] = bool(is_false)
    _check(new)
    entry.update(new)
    _save(_order(data))
    return entry


def add(start, end, level: str, is_false: bool = False) -> dict:
    """Додає вже завершену тривогу (напр., ту, яку бот пропустив через простій)."""
    entry = {"id": _new_id(), "start": _norm_ts(start), "end": _norm_ts(end), "level": level, "is_false": bool(is_false)}
    _check(entry)
    _save(_order(_load() + [entry]))
    return entry


def toggle_false(entry_id: str) -> bool:
    data = _load()
    entry = next((e for e in data if e["id"] == entry_id), None)
    if entry is None:
        raise KeyError("id")
    entry["is_false"] = not entry["is_false"]
    _save(data)
    return entry["is_false"]


def delete(entry_id: str) -> None:
    data = _load()
    new = [e for e in data if e["id"] != entry_id]
    if len(new) == len(data):
        raise KeyError("id")
    _save(new)


def months() -> list:
    """[{"month": "2026-10", "count": 7}] - від нових до старих (для очищення журналу по місяцях)."""
    counts: dict[str, int] = {}
    for e in _load():
        counts[e["start"][:7]] = counts.get(e["start"][:7], 0) + 1
    return [{"month": m, "count": c} for m, c in sorted(counts.items(), reverse=True)]


def clear_month(month: str, keep_open: bool = True) -> tuple[int, bool]:
    """Видаляє всі записи за місяць "YYYY-MM". Поточну (незакриту) тривогу лишає, якщо keep_open.
    Повертає (скільки видалено, чи залишено поточну)."""
    datetime.strptime(month, "%Y-%m")  # ValueError, якщо формат хибний
    keep, removed, kept_open = [], 0, False
    for e in _load():
        if e["start"][:7] == month:
            if keep_open and e["end"] is None:
                keep.append(e)
                kept_open = True
                continue
            removed += 1
        else:
            keep.append(e)
    if removed:
        _save(keep)
    return removed, kept_open
