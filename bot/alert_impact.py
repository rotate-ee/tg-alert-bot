"""
Скільки навчального часу "з'їли" тривоги: перетин інтервалу тривоги з
уроками (скасовані не враховуються, дострокове закінчення - враховується).
"""
from datetime import datetime, timedelta

import early_end
from lessons import KYIV_TZ, get_lessons_for_date


def _hm(d, s: str) -> datetime:
    h, m = map(int, s.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KYIV_TZ)


def session_impact(start: datetime, end: datetime | None, now: datetime) -> dict:
    """{"minutes": N, "lessons": [{"date","number","name","minutes"}]} для одного інтервалу."""
    end = end or now
    total, hit = 0.0, []
    d = start.date()
    while d <= end.date():
        for l in get_lessons_for_date(d):
            if l["cancelled"] or not l["start"]:
                continue
            ls = _hm(d, l["start"])
            le = _hm(d, early_end.get_early_end(d, l["number"]) or l["end"])
            overlap = (min(le, end) - max(ls, start)).total_seconds()
            if overlap > 0:
                total += overlap
                hit.append({"date": d.isoformat(), "number": l["number"], "name": l["name"],
                            "minutes": max(1, round(overlap / 60))})
        d += timedelta(days=1)
    return {"minutes": round(total / 60), "lessons": hit}


def window_impact(sessions: list[dict], window_start: datetime, now: datetime) -> dict:
    """Сумарний вплив усіх тривог, обрізаних по початку вікна (тиждень/місяць)."""
    minutes, lessons_hit = 0, 0
    for s in sessions:
        end = s["end"] or now
        if end <= window_start:
            continue
        imp = session_impact(max(s["start"], window_start), s["end"], now)
        minutes += imp["minutes"]
        lessons_hit += len(imp["lessons"])
    return {"lost_minutes": minutes, "lessons_hit": lessons_hit}
