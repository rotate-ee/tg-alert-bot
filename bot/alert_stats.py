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
    today = now.date()

    by_day: dict[str, dict] = {}
    by_hour = [0] * 24
    hours = {7: [0] * 24, 30: [0] * 24, 90: [0] * 24}
    by_month: dict[str, dict] = {}
    subjects: dict[str, dict] = {}
    spans = []
    longest = None
    night = total_minutes = hit_pairs = hit_minutes = alerts_with_hits = 0
    for e in entries:
        s, en = _span(e, now)
        spans.append((s, en))
        minutes = max(0, int((en - s).total_seconds() // 60))
        total_minutes += minutes
        is_night = s.hour >= 22 or s.hour < 6
        d = by_day.setdefault(e["start"][:10], {"count": 0, "minutes": 0, "red": 0, "yellow": 0, "night": 0})
        d["count"] += 1
        d["minutes"] += minutes
        d["red" if e["level"] == "red" else "yellow"] += 1
        d["night"] += 1 if is_night else 0
        by_hour[s.hour] += 1
        age = (today - s.date()).days
        for window, arr in hours.items():
            if age < window:
                arr[s.hour] += 1
        night += 1 if is_night else 0
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
            for h in hits:
                sub = subjects.setdefault(h["name"], {"name": h["name"], "count": 0, "minutes": 0})
                sub["count"] += 1
                sub["minutes"] += h["minutes"]

    days = []
    for i in range(179, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        v = by_day.get(d, {"count": 0, "minutes": 0, "red": 0, "yellow": 0, "night": 0})
        days.append({"date": d, **v})

    week = []  # 7 останніх днів: відрізки тривог (хвилини від півночі) для "теплової" смужки
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_start = datetime.combine(day, time(0, 0), tzinfo=KYIV_TZ)
        day_end = day_start + timedelta(days=1)
        segments = []
        for e, (s, en) in zip(entries, spans):
            lo, hi = max(s, day_start), min(en, day_end)
            if hi > lo:
                segments.append({"s": int((lo - day_start).total_seconds() // 60),
                                 "e": int((hi - day_start).total_seconds() // 60), "level": e["level"]})
        week.append({"date": day.isoformat(), "segments": segments})

    ordered = sorted(spans)
    gaps = [int((b[0] - a[1]).total_seconds() // 3600) for a, b in zip(ordered, ordered[1:]) if b[0] > a[1]]
    ongoing = any(e["end"] is None for e in entries)
    busiest = max(((d["count"], d["date"]) for d in days if d["count"]), default=None)
    records = {
        "longest": longest,
        "busiest_day": {"date": busiest[1], "count": busiest[0]} if busiest else None,
        "calm_longest_hours": max(gaps, default=None),
        "calm_current_hours": None if ongoing or not ordered else max(0, int((now - ordered[-1][1]).total_seconds() // 3600)),
    }
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
        "hours": {str(k): v for k, v in hours.items()},
        "week": week,
        "records": records,
        "subjects": sorted(subjects.values(), key=lambda x: (-x["count"], -x["minutes"]))[:5],
        "by_month": sorted(by_month.values(), key=lambda x: x["month"], reverse=True)[:12],
        "lessons": {"hit": hit_pairs, "minutes": hit_minutes, "alerts_with_hits": alerts_with_hits},
    }
