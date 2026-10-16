"""
Історія тривог - для вкладки "Тривоги" в Mini App і статистики.

Файл у DATA_DIR: список подій
[{"ts": "2026-10-15T12:00:00+03:00", "type": "started|level_changed|ended", "level": "red|yellow|null"}]

Ключова ідея - функція sync(): їй просто повідомляють поточний стан
(є тривога / немає, який рівень), а вона сама дописує потрібну подію.
Тому історія самовідновлюється: якщо бот перезапустився посеред тривоги
або пропустив відбій, наступний sync() все вирівняє, без дублів.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "alert_history.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

RETENTION_DAYS = 400
_SEVERITY = {None: 0, "yellow": 1, "red": 2}


def _load() -> list[dict]:
    if not DATA_PATH.exists():
        return []
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(events: list[dict]) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False)


def _ts(e: dict) -> datetime:
    return datetime.fromisoformat(e["ts"])


def _state(events: list[dict]) -> tuple[bool, str | None]:
    """(тривога відкрита?, останній відомий рівень) за журналом подій."""
    is_open, level = False, None
    for e in events:
        if e["type"] == "started":
            is_open, level = True, e.get("level")
        elif e["type"] == "level_changed" and is_open:
            level = e.get("level")
        elif e["type"] == "ended":
            is_open, level = False, None
    return is_open, level


def sync(active: bool, level: str | None, ts: datetime | None = None) -> str | None:
    """
    Привести журнал у відповідність до поточного стану.
    Повертає тип дописаної події ("started"/"level_changed"/"ended") або None.
    """
    ts = ts or datetime.now(KYIV_TZ)
    events = sorted(_load(), key=_ts)
    is_open, last_level = _state(events)

    if active and not is_open:
        kind = "started"
    elif active and is_open and level != last_level:
        kind = "level_changed"
    elif not active and is_open:
        kind = "ended"
    else:
        return None

    events.append({"ts": ts.isoformat(), "type": kind, "level": level if active else None})
    cutoff = ts - timedelta(days=RETENTION_DAYS)
    events = [e for e in events if _ts(e) >= cutoff]
    _save(events)
    return kind


def sessions() -> list[dict]:
    """
    Тривоги як інтервали: {"start": dt, "end": dt|None, "level": "red"|"yellow"|None}.
    level - найвищий рівень, що був під час тривоги.
    """
    result: list[dict] = []
    cur: dict | None = None
    for e in sorted(_load(), key=_ts):
        t = _ts(e)
        if e["type"] == "started":
            cur = {"start": t, "end": None, "level": e.get("level")}
            result.append(cur)
        elif e["type"] == "level_changed" and cur:
            if _SEVERITY.get(e.get("level"), 0) > _SEVERITY.get(cur["level"], 0):
                cur["level"] = e.get("level")
        elif e["type"] == "ended" and cur:
            cur["end"] = t
            cur = None
    return result


def open_since() -> str | None:
    """ISO-час початку поточної (ще не закритої) тривоги або None."""
    for s in reversed(sessions()):
        return s["start"].isoformat() if s["end"] is None else None
    return None


def _window_stats(all_sessions: list[dict], start: datetime, now: datetime) -> dict:
    count, total, longest = 0, 0.0, 0.0
    by_level = {"red": 0, "yellow": 0, "unknown": 0}
    for s in all_sessions:
        end = s["end"] or now
        lo, hi = max(s["start"], start), min(end, now)
        overlap = (hi - lo).total_seconds()
        if overlap <= 0:
            continue
        count += 1
        total += overlap
        longest = max(longest, overlap)
        by_level[s["level"] if s["level"] in ("red", "yellow") else "unknown"] += 1
    return {"count": count, "total_seconds": int(total), "longest_seconds": int(longest), "by_level": by_level}


def stats(now: datetime | None = None) -> dict:
    """Статистика за поточний тиждень (з понеділка) і місяць (з 1-го числа) + останні тривоги."""
    now = now or datetime.now(KYIV_TZ)
    all_sessions = sessions()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    recent = []
    for s in reversed(all_sessions[-10:]):
        end = s["end"] or now
        recent.append({
            "start": s["start"].isoformat(),
            "end": s["end"].isoformat() if s["end"] else None,
            "duration_seconds": int((end - s["start"]).total_seconds()),
            "level": s["level"],
        })

    return {
        "week": _window_stats(all_sessions, week_start, now),
        "month": _window_stats(all_sessions, month_start, now),
        "recent": recent,
    }
