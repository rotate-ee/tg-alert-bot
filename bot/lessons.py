"""
Работа с расписанием: загрузка schedule.json, определение "группы недель"
(1,2-й тиждень місяця / 3,4-й), поиск текущего/следующего урока и учёт
уроков, отменённых учителями (см. cancellations.py).
"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import bell_overrides
import cancellations
import config
import early_end
import extra_lessons
import holidays
import remote_overrides
import remote_lesson_overrides
import remote_periods
import renumber
import settings
import week_shifts

KYIV_TZ = ZoneInfo("Europe/Kyiv")
SCHEDULE_PATH = Path(__file__).parent / "schedule.json"


def load_schedule() -> dict:
    with open(SCHEDULE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    bells = {int(k): v for k, v in data["bells"].items()}
    days = {int(k): v for k, v in data["days"].items()}
    rooms = {int(k): v for k, v in data.get("rooms", {}).items()}
    remote_numbers = set(data.get("remote_lesson_numbers", []))
    return {"bells": bells, "days": days, "rooms": rooms, "remote_numbers": remote_numbers}


def _parse_time(value: str) -> time:
    h, m = map(int, value.split(":"))
    return time(hour=h, minute=m)


def is_lesson_remote_today(lesson: dict, d: date) -> bool:
    """
    Чи вважати урок дистанційним саме сьогодні.
    - Якщо урок взагалі не з "дистанційної" категорії (1-6 клас уроки) -
      завжди False.
    - Якщо тиждень вдома - технічно все дистанційно, але для таких
      уроків окрема позначка не потрібна (весь день і так вдома) - False.
    - Якщо очний тиждень (або чергування не налаштоване) і адмін
      підтвердив "Так, буде очно" на сьогодні - False.
    - Інакше (типовий випадок за замовчуванням) - True.
    """
    if remote_lesson_overrides.is_remote(d, lesson["number"]):
        return True
    if not lesson["remote"]:
        return False
    if is_school_week(d) is False:
        return False
    if remote_overrides.is_in_person(d, lesson["number"]):
        return False
    return True


def format_lesson_time(lesson: dict, d: date) -> str:
    """
    «11:00–11:45», «11:00–11:45 (дистанційно)» або просто «дистанційно»
    (якщо часу немає). Позначку "(дистанційно)" додаємо тільки якщо
    is_lesson_remote_today повертає True.
    """
    if lesson["start"] is None:
        return "дистанційно"
    base = f"{lesson['start']}–{lesson['end']}"
    return f"{base} (дистанційно)" if is_lesson_remote_today(lesson, d) else base


def format_lesson_room(lesson: dict, d: date) -> str:
    """
    ", каб. 303" - якщо є кабінет, урок не вважається дистанційним
    сьогодні (див. is_lesson_remote_today) і тиждень очний.
    Порожній рядок - якщо кабінет показувати не треба.
    """
    if not lesson["room"] or is_lesson_remote_today(lesson, d):
        return ""
    school = is_school_week(d)
    if school is False:
        return ""
    return f", каб. {lesson['room']}"


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


def _school_week_anchor_monday() -> date | None:
    raw = getattr(config, "SCHOOL_WEEK_ANCHOR_MONDAY", "").strip()
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None
    return d - timedelta(days=d.weekday())  # на всяк випадок приводимо до понеділка


def is_school_week(d: date) -> bool | None:
    """
    True - очний тиждень (в школі), False - тиждень вдома (дистанційно),
    None - чергування не налаштоване (SCHOOL_WEEK_ANCHOR_MONDAY порожній).
    Рахується від опорного понеділка: парна різниця тижнів - той самий
    тип, що й опорний (школа), непарна - протилежний (вдома).
    """
    if remote_periods.get_period(d):
        return False  # примусово дистанційний період (напр., тиждень після канікул)
    anchor = _school_week_anchor_monday()
    if anchor is None:
        return None
    monday = d - timedelta(days=d.weekday())
    return week_shifts.resolve(monday, anchor)


def week_type_label(d: date) -> str | None:
    school = is_school_week(d)
    if school is None:
        return None
    return "🏫 Очний тиждень (в школі)" if school else "🏠 Тиждень вдома (дистанційно)"


def _resolve_entry(entry, group: str) -> str | None:
    """Строка -> сам предмет/кабінет. None -> None (немає). Словарь {"1_2":.., "3_4":..} -> значення для поточної групи недель (або None, якщо для цієї групи нічого немає)."""
    if entry is None or isinstance(entry, str):
        return entry
    return entry.get(group)


def _build_day_lessons(
    day_entries: list,
    bells: dict,
    group: str,
    cancelled: set[int],
    remote_numbers: set[int],
    room_entries: list | None = None,
) -> list[dict]:
    """
    Разворачивает список предметов дня в список уроков с временем,
    пометкой "remote" (дистанційно - незалежно від наявності часу
    дзвінка), кабінетом ("room") і пометкой "cancelled" для уроків,
    скасованих учителями.
    """
    room_entries = room_entries or []
    lessons = []
    for idx, entry in enumerate(day_entries):
        number = idx + 1
        name = _resolve_entry(entry, group)
        if name is None:
            continue  # в эту группу недель этого урока просто нет

        bell = bells.get(number)
        room_entry = room_entries[idx] if idx < len(room_entries) else None
        lessons.append({
            "number": number,
            "name": name,
            "start": bell["start"] if bell else None,
            "end": bell["end"] if bell else None,
            "remote": bell is None or number in remote_numbers,
            "cancelled": number in cancelled,
            "room": _resolve_entry(room_entry, group),
        })
    return lessons


def schedule_ended(d: date) -> bool:
    """Розклад більше не діє (після випуску): дата пізніша за settings.schedule_end."""
    end = settings.get("schedule_end")
    return bool(end) and d.isoformat() > end


def zero_bell(schedule: dict) -> dict | None:
    """Типовий час 0-го уроку: 45 хв, що закінчується за 5 хв до початку 1-го уроку."""
    first = schedule["bells"].get(1)
    if not first:
        return None
    h, m = map(int, first["start"].split(":"))
    end = h * 60 + m - 5
    start = end - 45
    if start < 0:
        return None
    fmt = lambda x: f"{x // 60:02d}:{x % 60:02d}"
    return {"start": fmt(start), "end": fmt(end)}


def get_today_lessons(now: datetime | None = None) -> list[dict]:
    if now is None:
        now = datetime.now(KYIV_TZ)
    today = now.date()
    if holidays.get_holiday(today) or schedule_ended(today):
        return []  # канікули або розклад завершено - уроків немає
    schedule = load_schedule()
    group = week_group(now)
    day_entries = schedule["days"].get(now.weekday(), [])
    room_entries = schedule["rooms"].get(now.weekday(), [])
    cancelled = cancellations.get_cancelled_numbers(today)
    lessons = _build_day_lessons(
        day_entries, schedule["bells"], group, set(), schedule["remote_numbers"], room_entries
    )

    # разова перенумерація (/api, міні-застосунок): урок переходить в інший
    # слот дзвінка; скасування і зміни часу далі рахуються за НОВИМИ номерами
    rmap = renumber.get_map(today)
    for lesson in lessons:
        new_n = rmap.get(lesson["number"])
        if new_n is not None:
            bell = schedule["bells"].get(new_n) or (zero_bell(schedule) if new_n == 0 else None)
            lesson["number"] = new_n
            lesson["start"] = bell["start"] if bell else None
            lesson["end"] = bell["end"] if bell else None
            lesson["remote"] = bell is None or new_n in schedule["remote_numbers"]
    for lesson in lessons:
        lesson["cancelled"] = lesson["number"] in cancelled

    # разові зміни часу дзвінка (/settime, /endnow) - перекривають
    # стандартний час тільки для цього уроку і цієї дати
    for lesson in lessons:
        override = bell_overrides.get_override(today, lesson["number"])
        if override:
            lesson["start"] = override["start"]
            lesson["end"] = override["end"]

    # одноразові додаткові уроки (/addlesson), яких немає в schedule.json
    for extra in extra_lessons.get_extra_lessons(today):
        lessons = [l for l in lessons if l["number"] != extra["number"]]  # замінюємо, якщо збігається номер
        lessons.append({
            "number": extra["number"],
            "name": extra["name"],
            "start": extra["start"],
            "end": extra["end"],
            "remote": False,
            "cancelled": extra["number"] in cancelled,
            "room": None,
        })

    # сортуємо за часом початку, щоб додаткові уроки стали на своє місце
    # (наприклад, 0-й урок раніше 11:25 з'явиться на початку списку)
    lessons.sort(key=lambda l: l["start"] or "99:99")

    return lessons


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


def lesson_start_datetime(lesson: dict, d: date) -> datetime | None:
    """Дата+час початку уроку як datetime (для планування нагадувань). None - якщо часу немає."""
    if lesson["start"] is None:
        return None
    return datetime.combine(d, _parse_time(lesson["start"]), tzinfo=KYIV_TZ)


_DAY_NAMES = {
    0: "Понеділок",
    1: "Вівторок",
    2: "Середа",
    3: "Четвер",
    4: "П'ятниця",
    5: "Субота",
    6: "Неділя",
}


def format_cancelled_status(lesson: dict, d: date) -> str:
    """
    Позначка для скасованого уроку в розкладі на день/тиждень:
    у тиждень вдома - "(Класрум)" (урок не зник, а перейшов у Classroom),
    в очний тиждень (або якщо чергування не налаштоване) - "(скасовано)".
    Порожній рядок, якщо урок не скасовано.
    """
    if not lesson["cancelled"]:
        return ""
    if is_school_week(d) is False:
        return " (Класрум)"
    return " (скасовано)"


def format_week_message(now: datetime | None = None) -> str:
    """
    Формирует красиво оформлений розклад на тиждень
    (понеділок-п'ятниця; вихідні пропускаються, якщо уроків немає).
    У суботу/неділю показує вже НАСТУПНИЙ тиждень, бо поточний
    навчальний тиждень уже закінчився.
    """
    if now is None:
        now = datetime.now(KYIV_TZ)

    monday = now.date() - timedelta(days=now.weekday())
    if now.weekday() >= 5:  # 5=субота, 6=неділя
        monday += timedelta(days=7)

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
            time_part = format_lesson_time(lesson, d)
            room_part = format_lesson_room(lesson, d)
            status = format_cancelled_status(lesson, d)
            lines.append(f"{lesson['number']}. {lesson['name']} — {time_part}{room_part}{status}")

        blocks.append("\n".join(lines))

    title = f"🗓 Розклад на тиждень ({monday.strftime('%d.%m')}–{(monday + timedelta(days=4)).strftime('%d.%m')})"
    week_label = week_type_label(monday)
    if week_label:
        title += f"\n{week_label}"

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
        # /endnow міг позначити, що урок закінчився раніше - це впливає
        # тільки тут (пошук поточного/наступного), сам lesson["end"] і
        # відображуваний розклад лишаються без змін
        early = early_end.get_early_end(now.date(), lesson["number"])
        end = _parse_time(early) if early else _parse_time(lesson["end"])
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
    if now is None:
        now = datetime.now(KYIV_TZ)

    current, upcoming, remote_no_time_today, cancelled_today = find_current_and_next(now)

    if current:
        body = (
            f"Зараз: {current['number']}-й урок «{current['name']}», "
            f"{format_lesson_time(current, now.date())}{format_lesson_room(current, now.date())}"
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
        time_part = format_lesson_time(lesson, now.date())
        room_part = format_lesson_room(lesson, now.date())
        status = format_cancelled_status(lesson, now.date())
        lines.append(f"{lesson['number']}. {lesson['name']} — {time_part}{room_part}{status}")

    body = "\n".join(lines)
    week_label = week_type_label(now.date())
    if week_label:
        body = f"{week_label}\n\n{body}"

    return f"{prefix}\n\n{body}"
