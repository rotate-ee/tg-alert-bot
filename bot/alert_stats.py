"""Статистика тривог та їх вплив на уроки (на основі журналу alert_log і розкладу)."""
import time as _time
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import alert_log
import early_end
import holidays
import lessons

KYIV_TZ = ZoneInfo("Europe/Kyiv")
_cache: dict = {}


def _cached(key, ttl: float, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = (_time.time(), value)
    return value


def _span(entry: dict, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(entry["start"])
    end = datetime.fromisoformat(entry["end"]) if entry["end"] else now
    return start, max(end, start)


def _hhmm(d: date, value: str) -> datetime:
    h, m = map(int, value.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=KYIV_TZ)


def _lessons_hit(entry: dict, now: datetime) -> list[dict]:
    start, end = _span(entry, now)
    out: dict[tuple, dict] = {}
    day = start.date()
    while day <= end.date():
        window_start = max(start, datetime.combine(day, time(8, 0), tzinfo=KYIV_TZ))
        window_end = min(end, datetime.combine(day, time(20, 0), tzinfo=KYIV_TZ))
        if (window_end > window_start and day.weekday() < 5
                and not holidays.get_holiday(day) and not lessons.schedule_ended(day)):
            for lesson in lessons.get_lessons_for_date(day):
                if lesson["cancelled"] or not lesson["start"] or not lesson["end"]:
                    continue
                lesson_end = early_end.get_early_end(day, lesson["number"]) or lesson["end"]
                overlap = (min(end, _hhmm(day, lesson_end)) - max(start, _hhmm(day, lesson["start"]))).total_seconds() / 60
                if overlap >= 1:
                    out[(lesson["number"], lesson["name"])] = {
                        "number": lesson["number"], "name": lesson["name"], "minutes": int(overlap)}
        day += timedelta(days=1)
    return sorted(out.values(), key=lambda x: x["number"])


def lessons_hit(entry: dict, now: datetime | None = None) -> list[dict]:
    """Які уроки перервала тривога (з кешем: завершені записи - 10 хв, поточна - 1 хв)."""
    now = now or datetime.now(KYIV_TZ)
    ttl = 600 if entry["end"] else 60
    return _cached(("hit", entry["id"], entry["start"], entry["end"]), ttl, lambda: _lessons_hit(entry, now))


def compute(now: datetime | None = None) -> dict:
    now = now or datetime.now(KYIV_TZ)
    key = ("stats", int(_time.time() // 60), len(alert_log.all_entries()),
           alert_log.DATA_PATH.stat().st_mtime_ns if alert_log.DATA_PATH.exists() else 0)
    return _cached(key, 60, lambda: _compute(now))


def _compute(now: datetime) -> dict:
    cutoff = now - timedelta(days=366)
    all_in_window = [e for e in alert_log.all_entries() if datetime.fromisoformat(e["start"]) >= cutoff]
    entries = [e for e in all_in_window if not e.get("is_false")]
    false_count = len(all_in_window) - len(entries)

    by_day: dict[str, list[int]] = {}
    by_hour = [0] * 24
    by_month: dict[str, dict] = {}
    longest = None
    night = total_minutes = hit_pairs = hit_minutes = alerts_with_hits = 0
    for e in entries:
        s, en = _span(e, now)
        minutes = max(0, int((en - s).total_seconds() // 60))
        total_minutes += minutes
        d = e["start"][:10]
        by_day.setdefault(d, [0, 0])
        by_day[d][0] += 1
        by_day[d][1] += minutes
        by_hour[s.hour] += 1
        if s.hour >= 22 or s.hour < 6:
            night += 1
        if longest is None or minutes > longest["minutes"]:
            longest = {"minutes": minutes, "start": e["start"]}
        m = by_month.setdefault(e["start"][:7], {"month": e["start"][:7], "count": 0, "minutes": 0,
                                                 "lessons_hit": 0, "lessons_minutes": 0})
        m["count"] += 1
        m["minutes"] += minutes
        hits = lessons_hit(e, now)
        if hits:
            alerts_with_hits += 1
            m["lessons_hit"] += len(hits)
            m["lessons_minutes"] += sum(h["minutes"] for h in hits)
            hit_pairs += len(hits)
            hit_minutes += sum(h["minutes"] for h in hits)

    today = now.date()
    days = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        c, mn = by_day.get(d, [0, 0])
        days.append({"date": d, "count": c, "minutes": mn})
    return {
        "generated": now.isoformat(timespec="seconds"),
        "totals": {
            "count": len(entries), "minutes": total_minutes,
            "avg": round(total_minutes / len(entries)) if entries else 0,
            "longest": longest, "night": night, "false": false_count,
            "days_with_alerts": len(by_day),
            "since": min((e["start"][:10] for e in entries), default=None),
        },
        "by_day": days,
        "by_hour": by_hour,
        "by_month": sorted(by_month.values(), key=lambda x: x["month"], reverse=True)[:12],
        "lessons": {"hit": hit_pairs, "minutes": hit_minutes, "alerts_with_hits": alerts_with_hits},
    }
