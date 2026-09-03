"""
Работа с расписанием: загрузка schedule.json, определение "группы недель"
(1,2-й тиждень місяця / 3,4-й) и поиск текущего / следующего урока.
"""
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import config

KYIV_TZ = ZoneInfo("Europe/Kyiv")
SCHEDULE_PATH = Path(__file__).parent / "schedule.json"


def load_schedule() -> dict:
    with open(SCHEDULE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    bells = {int(k): v for k, v in data["bells"].items()}
    days = {int(k): v for k, v in data["days"].items()}
    return {"bells": bells, "days": days}


def _parse_time(value: str) -> time:
    h, m = map(int, value.split(":"))
    return time(hour=h, minute=m)


def week_group(now: datetime) -> str:
    """
    Возвращает "1_2" или "3_4" - какая половина учебных недель месяца
    сейчас действует. Можно принудительно задать через
    WEEK_GROUP_OVERRIDE в .env, если автоматический расчёт не совпадает
    с реальным журналом школы.
    """
    override = getattr(config, "WEEK_GROUP_OVERRIDE", "auto")
    if override in ("1_2", "3_4"):
        return override

    week_of_month = ((now.day - 1) // 7) + 1  # 1..5
    return "1_2" if week_of_month in (1, 2) else "3_4"


def _resolve_entry(entry, group: str) -> str | None:
    """Строка -> сам предмет. Словарь {"1_2":.., "3_4":..} -> предмет для текущей группы недель (или None, если урока в эту группу нет)."""
    if isinstance(entry, str):
        return entry
    return entry.get(group)


def _build_day_lessons(day_entries: list, bells: dict, group: str) -> list[dict]:
    """
    Разворачивает список предметов дня в список уроков с временем
    (для 1-6 урока) либо пометкой "дистанційно" (для 7-го и далее,
    т.к. в розкладі дзвінків для них нет фиксированного времени).
    """
    lessons = []
    for idx, entry in enumerate(day_entries):
        number = idx + 1
        name = _resolve_entry(entry, group)
        if name is None:
            continue  # в эту группу недель этого урока просто нет

        bell = bells.get(number)
        if bell:
            lessons.append({
                "number": number,
                "name": name,
                "start": bell["start"],
                "end": bell["end"],
                "remote": False,
            })
        else:
            lessons.append({
                "number": number,
                "name": name,
                "start": None,
                "end": None,
                "remote": True,
            })
    return lessons


def get_today_lessons(now: datetime | None = None) -> list[dict]:
    if now is None:
        now = datetime.now(KYIV_TZ)
    schedule = load_schedule()
    group = week_group(now)
    day_entries = schedule["days"].get(now.weekday(), [])
    return _build_day_lessons(day_entries, schedule["bells"], group)


def find_current_and_next(now: datetime | None = None):
    """
    Возвращает (текущий_урок, следующий_урок, список_дистанційних_уроків)
    на момент `now`. Дистанційні уроки (без часу) не участвуют в
    поиске "текущий/следующий" - для них нет привязки к часам.
    """
    if now is None:
        now = datetime.now(KYIV_TZ)

    today_lessons = get_today_lessons(now)
    now_t = now.time()

    current = None
    upcoming = None
    remote_today = []

    for lesson in today_lessons:
        if lesson["remote"]:
            remote_today.append(lesson)
            continue
        start = _parse_time(lesson["start"])
        end = _parse_time(lesson["end"])
        if start <= now_t <= end:
            current = lesson
        elif start > now_t and upcoming is None:
            upcoming = lesson

    return current, upcoming, remote_today


def format_status_message(prefix: str, now: datetime | None = None) -> str:
    """
    Формирует итоговый текст сообщения (например, об отбое тревоги)
    с указанием текущего/следующего урока и, если есть, дистанційних
    уроків сьогодні.
    """
    current, upcoming, remote_today = find_current_and_next(now)

    if current:
        body = (
            f"Зараз: {current['number']}-й урок «{current['name']}» "
            f"({current['start']}–{current['end']})"
        )
    elif upcoming:
        body = (
            f"Уроків зараз немає. Наступний — {upcoming['number']}-й урок "
            f"«{upcoming['name']}» о {upcoming['start']}"
        )
    else:
        body = "Уроків на сьогодні більше немає."

    if remote_today:
        names = ", ".join(f"{l['number']}-й «{l['name']}»" for l in remote_today)
        body += f"\n\n(Дистанційно сьогодні також: {names})"

    return f"{prefix}\n\n{body}"
