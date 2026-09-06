"""
Работа с расписанием: загрузка schedule.json, определение "группы недель"
(1,2-й тиждень місяця / 3,4-й), поиск текущего/следующего урока и учёт
уроков, отменённых учителями (см. cancellations.py).
"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cancellations
import config

KYIV_TZ = ZoneInfo("Europe/Kyiv")
SCHEDULE_PATH = Path(__file__).parent / "schedule.json"


def load_schedule() -> dict:
    with open(SCHEDULE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    bells = {int(k): v for k, v in data["bells"].items()}
    days = {int(k): v for k, v in data["days"].items()}
    remote_numbers = set(data.get("remote_lesson_numbers", []))
    return {"bells": bells, "days": days, "remote_numbers": remote_numbers}


def _parse_time(value: str) -> time:
    h, m = map(int, value.split(":"))
    return time(hour=h, minute=m)


def _format_lesson_time(lesson: dict) -> str:
    """«11:00–11:45», «11:00–11:45 (дистанційно)» або просто «дистанційно» (якщо часу немає)."""
    if lesson["start"] is None:
        return "дистанційно"
    base = f"{lesson['start']}–{lesson['end']}"
    return f"{base} (дистанційно)" if lesson["remote"] else base


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


def _build_day_lessons(
    day_entries: list, bells: dict, group: str, cancelled: set[int], remote_numbers: set[int]
) -> list[dict]:
    """
    Разворачивает список предметов дня в список уроков с временем,
    пометкой "remote" (дистанційно - незалежно від наявності часу
    дзвінка) и пометкой "cancelled" для уроків, скасованих учителями.
    """
    lessons = []
    for idx, entry in enumerate(day_entries):
        number = idx + 1
        name = _resolve_entry(entry, group)
        if name is None:
            continue  # в эту группу недель этого урока просто нет

        bell = bells.get(number)
        lessons.append({
            "number": number,
            "name": name,
            "start": bell["start"] if bell else None,
            "end": bell["end"] if bell else None,
            "remote": bell is None or number in remote_numbers,
            "cancelled": number in cancelled,
        })
    return lessons


def get_today_lessons(now: datetime | None = None) -> list[dict]:
    if now is None:
        now = datetime.now(KYIV_TZ)
    schedule = load_schedule()
    group = week_group(now)
    day_entries = schedule["days"].get(now.weekday(), [])
    cancelled = cancellations.get_cancelled_numbers(now.date())
    return _build_day_lessons(
        day_entries, schedule["bells"], group, cancelled, schedule["remote_numbers"]
    )


def get_lessons_for_date(d: date) -> list[dict]:
    """Список уроков на конкретную дату (с учётом чергування тижнів і скасувань)."""
    now = datetime.combine(d, time(12, 0), tzinfo=KYIV_TZ)
    return get_today_lessons(now)


def get_lesson_by_number(number: int, d: date) -> dict | None:
    """Найти урок с данным номером на конкретную дату (для команд /cancel, /uncancel)."""
    for lesson in get_lessons_for_date(d):
        if lesson["number"] == number:
            return lesson
    return None


_DAY_NAMES = {
    0: "Понеділок",
    1: "Вівторок",
    2: "Середа",
    3: "Четвер",
    4: "П'ятниця",
    5: "Субота",
    6: "Неділя",
}


def format_week_message(now: datetime | None = None) -> str:
    """
    Формирует красиво оформлений розклад на весь поточний тиждень
    (понеділок-п'ятниця; вихідні пропускаються, якщо уроків немає).
    """
    if now is None:
        now = datetime.now(KYIV_TZ)

    monday = now.date() - timedelta(days=now.weekday())

    blocks = []
    for offset in range(7):
        d = monday + timedelta(days=offset)
        lessons = get_lessons_for_date(d)
        if not lessons:
            continue

        day_name = _DAY_NAMES[d.weekday()]
        is_today = d == now.date()
        header = f"📌 {day_name}, {d.strftime('%d.%m')}" + (" (сьогодні)" if is_today else "")

        lines = [header, "─" * 22]
        for lesson in lessons:
            time_part = _format_lesson_time(lesson)
            status = " ❌" if lesson["cancelled"] else ""
            lines.append(f"{lesson['number']}. {lesson['name']} — {time_part}{status}")

        blocks.append("\n".join(lines))

    title = f"🗓 Розклад на тиждень ({monday.strftime('%d.%m')}–{(monday + timedelta(days=4)).strftime('%d.%m')})"

    if not blocks:
        return f"{title}\n\nУроків на цьому тижні немає."

    return title + "\n\n" + "\n\n".join(blocks)


def find_current_and_next(now: datetime | None = None):
    """
    Возвращает (текущий_урок, следующий_урок, дистанційні_сьогодні,
    скасовані_сьогодні) на момент `now`. Дистанційні уроки (без часу)
    и скасовані уроки не участвуют в поиске "текущий/следующий".
    """
    if now is None:
        now = datetime.now(KYIV_TZ)

    today_lessons = get_today_lessons(now)
    now_t = now.time()

    current = None
    upcoming = None
    remote_no_time_today = []
    cancelled_today = []

    for lesson in today_lessons:
        if lesson["cancelled"]:
            cancelled_today.append(lesson)
            continue
        if lesson["start"] is None:
            # дистанційний урок без фіксованого часу - в пошук поточного/наступного не потрапляє
            remote_no_time_today.append(lesson)
            continue
        start = _parse_time(lesson["start"])
        end = _parse_time(lesson["end"])
        if start <= now_t <= end:
            current = lesson
        elif start > now_t and upcoming is None:
            upcoming = lesson

    return current, upcoming, remote_no_time_today, cancelled_today


def format_status_message(prefix: str, now: datetime | None = None) -> str:
    """
    Формирует итоговый текст сообщения (например, об отбое тревоги)
    с указанием текущего/следующего урока и, если есть, дистанційних
    без часу та скасованих уроків сьогодні.
    """
    current, upcoming, remote_no_time_today, cancelled_today = find_current_and_next(now)

    if current:
        body = (
            f"Зараз: {current['number']}-й урок «{current['name']}», "
            f"{_format_lesson_time(current)}"
        )
    elif upcoming:
        body = (
            f"Уроків зараз немає. Наступний — {upcoming['number']}-й урок "
            f"«{upcoming['name']}» о {upcoming['start']}"
        )
    else:
        body = "Уроків на сьогодні більше немає."

    if remote_no_time_today:
        names = ", ".join(f"{l['number']}-й «{l['name']}»" for l in remote_no_time_today)
        body += f"\n\n(Дистанційно сьогодні також: {names})"

    if cancelled_today:
        names = ", ".join(f"{l['number']}-й «{l['name']}»" for l in cancelled_today)
        body += f"\n\n(Скасовано сьогодні: {names})"

    return f"{prefix}\n\n{body}"


def format_day_schedule_message(prefix: str, now: datetime | None = None) -> str | None:
    """
    Формирует список всех уроков на сьогодні (для ранкової розсилки).
    Возвращает None, если на сьогодні уроків немає взагалі (вихідний) -
    тоді розсилку можна не відправляти, щоб не спамити в вихідні.
    """
    if now is None:
        now = datetime.now(KYIV_TZ)

    today_lessons = get_today_lessons(now)
    if not today_lessons:
        return None

    lines = []
    for lesson in today_lessons:
        time_part = _format_lesson_time(lesson)
        status = " (скасовано)" if lesson["cancelled"] else ""
        lines.append(f"{lesson['number']}. {lesson['name']} — {time_part}{status}")

    return f"{prefix}\n\n" + "\n".join(lines)
