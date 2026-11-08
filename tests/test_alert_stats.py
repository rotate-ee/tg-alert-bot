from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import alert_log
import alert_stats
import lessons

K = ZoneInfo("Europe/Kyiv")
MONDAY = date(2026, 10, 19)


def first_lesson():
    return next(l for l in lessons.get_lessons_for_date(MONDAY) if not l["cancelled"])


def at(d, hhmm, plus=0):
    h, m = map(int, hhmm.split(":"))
    return (datetime(d.year, d.month, d.day, h, m, tzinfo=K) + timedelta(minutes=plus)).strftime("%Y-%m-%d %H:%M")


def test_alert_during_lesson_is_detected():
    lesson = first_lesson()
    entry = alert_log.add(at(MONDAY, lesson["start"], 5), at(MONDAY, lesson["start"], 15), "red")
    hits = alert_stats.lessons_hit(alert_log.get(entry["id"]))
    assert [h["number"] for h in hits] == [lesson["number"]]
    assert hits[0]["minutes"] == 10


def test_alert_outside_lessons_and_on_weekend_has_no_hits():
    night = alert_log.add(at(MONDAY, "03:00"), at(MONDAY, "03:40"), "red")
    sunday = alert_log.add("2026-10-18 13:00", "2026-10-18 13:30", "red")
    assert alert_stats.lessons_hit(alert_log.get(night["id"])) == []
    assert alert_stats.lessons_hit(alert_log.get(sunday["id"])) == []


def test_cancelled_lesson_is_not_counted():
    import cancellations
    lesson = first_lesson()
    cancellations.cancel_lesson(MONDAY, lesson["number"])
    entry = alert_log.add(at(MONDAY, lesson["start"], 5), at(MONDAY, lesson["start"], 15), "red")
    assert all(h["number"] != lesson["number"] for h in alert_stats.lessons_hit(alert_log.get(entry["id"])))


def test_compute_totals_false_excluded_and_night_counted():
    lesson = first_lesson()
    alert_log.add(at(MONDAY, lesson["start"], 5), at(MONDAY, lesson["start"], 25), "red")       # 20 хв, під час уроку
    alert_log.add(at(MONDAY, "23:10"), at(MONDAY, "23:40"), "yellow")                            # 30 хв, нічна
    bad = alert_log.add(at(MONDAY, "15:00"), at(MONDAY, "16:00"), "red", is_false=True)          # хибна
    stats = alert_stats.compute(now=datetime(2026, 10, 25, 12, 0, tzinfo=K))
    t = stats["totals"]
    assert (t["count"], t["minutes"], t["night"], t["false"]) == (2, 50, 1, 1)
    assert t["longest"]["minutes"] == 30
    assert stats["lessons"]["alerts_with_hits"] == 1 and stats["lessons"]["minutes"] == 20
    assert len(stats["by_day"]) == 180 and sum(d["count"] for d in stats["by_day"]) == 2
    assert len(stats["by_hour"]) == 24 and stats["by_hour"][23] == 1
    assert stats["by_month"][0]["month"] == "2026-10" and stats["by_month"][0]["count"] == 2
    assert bad["id"] not in str(stats)


def test_compute_empty_log():
    stats = alert_stats.compute(now=datetime(2026, 10, 25, 12, 0, tzinfo=K))
    assert stats["totals"]["count"] == 0 and stats["totals"]["longest"] is None


def test_dashboard_data_levels_hours_week_records_subjects():
    lesson = first_lesson()
    alert_log.add(at(MONDAY, lesson["start"], 5), at(MONDAY, lesson["start"], 25), "red")
    alert_log.add(at(MONDAY, "23:10"), at(MONDAY, "23:40"), "yellow")
    alert_log.add(at(date(2026, 10, 22), "04:00"), at(date(2026, 10, 22), "05:00"), "red")
    now = datetime(2026, 10, 25, 12, 0, tzinfo=K)
    stats = alert_stats.compute(now=now)
    day = next(d for d in stats["by_day"] if d["date"] == "2026-10-19")
    assert (day["count"], day["red"], day["yellow"], day["night"], day["minutes"]) == (2, 1, 1, 1, 50)
    assert set(stats["hours"]) == {"7", "30", "90"} and sum(stats["hours"]["7"]) == 3 and stats["hours"]["7"][4] == 1
    assert len(stats["week"]) == 7 and stats["week"][-1]["date"] == "2026-10-25"
    mon = next(w for w in stats["week"] if w["date"] == "2026-10-19")
    assert sorted((x["level"]) for x in mon["segments"]) == ["red", "yellow"]
    assert all(0 <= x["s"] < x["e"] <= 1440 for w in stats["week"] for x in w["segments"])
    r = stats["records"]
    assert r["busiest_day"] == {"date": "2026-10-19", "count": 2} and r["longest"]["minutes"] == 60
    # з 05:00 22.10 до 12:00 25.10 - 79 год на годиннику, але 25.10.2026 у Києві переводять годинник назад (+1 год)
    assert r["calm_current_hours"] == 80
    assert r["calm_longest_hours"] == 52          # з 23:40 пн до 04:00 чт
    assert stats["subjects"][0]["name"] == lesson["name"] and stats["subjects"][0]["count"] == 1


def test_alert_crossing_midnight_is_split_in_week_strip():
    alert_log.add("2026-10-24 23:30", "2026-10-25 00:30", "red")
    stats = alert_stats.compute(now=datetime(2026, 10, 25, 12, 0, tzinfo=K))
    saturday = next(w for w in stats["week"] if w["date"] == "2026-10-24")
    sunday = next(w for w in stats["week"] if w["date"] == "2026-10-25")
    assert saturday["segments"] == [{"s": 23 * 60 + 30, "e": 1440, "level": "red"}]
    assert sunday["segments"] == [{"s": 0, "e": 30, "level": "red"}]
