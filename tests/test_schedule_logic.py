from datetime import date

import holidays
import lessons
import settings
import week_shifts


def school(day: str) -> bool:
    return lessons.is_school_week(date.fromisoformat(day))


def test_autumn_2026_week_shift_is_seeded():
    # після осінніх канікул: 02.11 дистанційний, далі очний і чергуються
    assert [school(d) for d in ("2026-10-19", "2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23")] == [True, False, True, False, True]


def test_shift_can_be_replaced_and_removed():
    week_shifts.add(date(2026, 12, 16), True)             # понеділок тижня 14.12
    assert school("2026-12-14") is True and school("2026-12-21") is False
    for s in week_shifts.list_all():
        week_shifts.remove(s["id"])
    assert school("2026-11-09") is False                   # знову базове чергування від опорної дати


def test_summer_is_holiday_and_school_year_ends():
    assert holidays.get_holiday(date(2027, 7, 15))["title"] == "Літні канікули"
    assert holidays.get_holiday(date(2026, 9, 7)) is None
    assert lessons.schedule_ended(date(2027, 9, 1)) is True
    assert lessons.schedule_ended(date(2027, 5, 31)) is False
    settings.set_value("schedule_end", "")
    assert lessons.schedule_ended(date(2030, 1, 1)) is False


def test_zero_lesson_gets_default_time_before_first_lesson():
    day = date(2026, 10, 19)
    first = lessons.zero_bell(lessons.load_schedule())
    bell1 = lessons.load_schedule()["bells"][1]
    assert first["end"] < bell1["start"] and first["start"] < first["end"]
