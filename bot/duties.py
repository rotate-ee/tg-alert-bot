"""Щоденна черга пар для класного чергування."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
import duty_overrides
import holidays

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def upcoming(limit: int = 5, today: date | None = None) -> list[dict]:
    students = config.DUTY_STUDENTS
    if not students:
        return []

    anchor = date.fromisoformat(config.DUTY_START_DATE)
    today = today or datetime.now(KYIV_TZ).date()
    current = max(anchor, today)

    # Визначаємо номер чергового навчального дня від початку графіка.
    slot = 0
    cursor = anchor
    while cursor < current:
        if cursor.weekday() < 5 and holidays.get_holiday(cursor) is None:
            slot += 1
        cursor += timedelta(days=1)

    result = []
    while len(result) < limit:
        if current.weekday() < 5 and holidays.get_holiday(current) is None:
            first = slot * 2 % len(students)
            pair = [students[first], students[(first + 1) % len(students)]]
            override = duty_overrides.get_override(current)
            result.append({
                "date": current.isoformat(),
                "students": override or pair,
                "overridden": override is not None,
            })
            slot += 1
        current += timedelta(days=1)
    return result
