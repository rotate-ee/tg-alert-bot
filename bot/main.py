"""
Telegram-бот: знает расписание уроков класса и после отбоя воздушной
тревоги пишет в чат "Відбій тривоги" + текущий/следующий урок со временем.

Запуск:
    python bot/main.py
"""
import asyncio
import logging
import os
import time as _time
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

import alert_history
import bell_overrides
import cancellations
import config
import early_end
import extra_lessons
import holidays
import remote_overrides
import settings
from alerts import AlertWatcher
from webapp import WebAppDeps, create_app, start_server
from lessons import (
    find_current_and_next,
    format_day_schedule_message,
    format_lesson_room,
    format_lesson_time,
    format_status_message,
    format_week_message,
    get_lesson_by_number,
    get_today_lessons,
    lesson_start_datetime,
    is_school_week,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

alert_watcher = AlertWatcher(
    token=config.ALERTS_TOKEN,
    region_title=config.REGION_TITLE,
    parent_title=config.REGION_PARENT_TITLE,
    extra_titles=config.REGION_EXTRA_TITLES,
    nearby_oblasts=config.NEARBY_OBLASTS,
)


# ---------- команды для ручной проверки в чате ----------

@dp.message(Command("now"))
async def cmd_now(message: Message):
    text = format_status_message("📚 Розклад")
    await message.answer(text)


@dp.message(Command("today"))
async def cmd_today(message: Message):
    today = datetime.now(KYIV_TZ).date()
    hol = holidays.holiday_on(today)
    if hol:
        end = date.fromisoformat(hol["end"])
        await message.answer(f"🏖 {hol['name']} - до {end.strftime('%d.%m')} включно. Уроків немає.")
        return
    text = format_day_schedule_message("📅 Розклад на сьогодні")
    if text is None:
        text = "Сьогодні уроків немає (вихідний)."
    await message.answer(text)


@dp.message(Command("week"))
async def cmd_week(message: Message):
    text = format_week_message()
    await message.answer(text)


@dp.message(Command("start", "help"))
async def cmd_help(message: Message):
    text = (
        "Привіт! Я стежу за повітряною тривогою в "
        f"«{config.REGION_TITLE}» і після відбою пишу сюди, "
        "який зараз урок.\n\n"
        "Команди:\n"
        "/now — показати поточний/наступний урок прямо зараз\n"
        "/today — повний розклад на сьогодні\n"
        "/week — розклад на весь тиждень"
        + ("\n/app — відкрити застосунок (розклад, тривоги)" if config.WEBAPP_URL else "")
    )
    if message.chat.type == "private" and message.from_user.id in config.ADMIN_IDS:
        text += (
            "\n\nКоманди для відмітки скасованих уроків (тільки в ЛС):\n"
            "/cancel <номер> — скасувати урок сьогодні (наприклад /cancel 3)\n"
            "/cancel <номер> <РРРР-ММ-ДД> — скасувати урок на конкретну дату\n"
            "/uncancel <номер> [дата] — повернути урок назад\n"
            "/cancelled [дата] — список скасованих на день\n"
            "/diag — перевірити, чи зберігаються скасування на диску\n"
            "/settime <номер> <початок> <кінець> [дата] — змінити час уроку на цей день\n"
            "/resettime <номер> [дата] — повернути стандартний час уроку\n"
            "/addlesson <номер> <початок> <кінець> <назва> [дата] — додати позаплановий урок\n"
            "/removelesson <номер> [дата] — прибрати доданий урок\n"
            "/announce <текст> — надіслати оголошення в груповий чат\n"
            "/endnow [номер] — позначити, що урок закінчився саме зараз\n"
            "/undoend <номер> [дата] — прибрати позначку дострокового завершення\n"
            "/holiday add <від> <до> <назва> | list | remove <від> — канікули"
        )
    await message.answer(text)


@dp.message(Command("app"))
async def cmd_app(message: Message):
    if not config.WEBAPP_URL:
        await message.answer("Застосунок ще не налаштовано (потрібна змінна WEBAPP_URL).")
        return
    if message.chat.type != "private":
        await message.answer("Відкрийте бота в особистих повідомленнях і напишіть /app")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Відкрити розклад", web_app=WebAppInfo(url=config.WEBAPP_URL))
    ]])
    await message.answer("Розклад, тривоги та адмін-панель:", reply_markup=kb)


# ---------- команды отмены уроков (только в ЛС, только для админов) ----------

def _parse_date_arg(arg: str | None) -> date | None:
    if not arg:
        return datetime.now(KYIV_TZ).date()
    try:
        return date.fromisoformat(arg)
    except ValueError:
        return None


def _is_allowed(message: Message) -> bool:
    return message.chat.type == "private" and message.from_user.id in config.ADMIN_IDS


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if not _is_allowed(message):
        return  # молча игнорируем чужих/групповые чаты

    parts = message.text.split()[1:]
    if not parts:
        await message.answer("Використання: /cancel <номер> [дата РРРР-ММ-ДД]")
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /cancel 3")
        return

    target_date = _parse_date_arg(parts[1] if len(parts) > 1 else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /cancel 3 2026-09-10")
        return

    lesson = get_lesson_by_number(number, target_date)
    if lesson is None:
        await message.answer(f"На {target_date.isoformat()} немає {number}-го уроку за розкладом.")
        return

    cancellations.cancel_lesson(target_date, number)
    await message.answer(
        f"✅ Скасовано: {number}-й урок «{lesson['name']}» ({target_date.isoformat()})"
    )


@dp.message(Command("uncancel"))
async def cmd_uncancel(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    if not parts:
        await message.answer("Використання: /uncancel <номер> [дата РРРР-ММ-ДД]")
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /uncancel 3")
        return

    target_date = _parse_date_arg(parts[1] if len(parts) > 1 else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /uncancel 3 2026-09-10")
        return

    restored = cancellations.uncancel_lesson(target_date, number)
    if restored:
        await message.answer(f"↩️ Урок {number} знову активний ({target_date.isoformat()})")
    else:
        await message.answer(f"Урок {number} і так не був скасований на {target_date.isoformat()}.")


@dp.message(Command("cancelled"))
async def cmd_cancelled(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    target_date = _parse_date_arg(parts[0] if parts else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /cancelled 2026-09-10")
        return

    numbers = sorted(cancellations.get_cancelled_numbers(target_date))
    if not numbers:
        await message.answer(f"На {target_date.isoformat()} скасованих уроків немає.")
        return

    lines = []
    for n in numbers:
        lesson = get_lesson_by_number(n, target_date)
        name = lesson["name"] if lesson else "?"
        lines.append(f"{n}-й «{name}»")
    await message.answer(f"Скасовано на {target_date.isoformat()}:\n" + "\n".join(lines))


@dp.message(Command("holiday"))
async def cmd_holiday(message: Message):
    if not _is_allowed(message):
        return
    rest = message.text.split(maxsplit=1)[1:]
    args = rest[0].split() if rest else []
    sub = args[0].lower() if args else "list"

    if sub == "list":
        items = holidays.upcoming(datetime.now(KYIV_TZ).date())
        if not items:
            await message.answer("Канікул не заплановано. Додати: /holiday add 2026-10-26 2026-11-01 Осінні канікули")
            return
        def line(h):
            tail = {"home": " → потім дистанційно", "school": " → потім очно"}.get(h.get("after"), "")
            if tail and int(h.get("after_weeks") or 1) > 1:
                tail += f" ({h['after_weeks']} тиж.)"
            return f"{h['start']} – {h['end']}: {h['name']}{tail}"
        await message.answer("🏖 Канікули:\n" + "\n".join(line(h) for h in items))
    elif sub == "add" and len(args) >= 3:
        start, end = _parse_date_arg(args[1]), _parse_date_arg(args[2])
        if start is None or end is None:
            await message.answer("Дати мають бути у форматі РРРР-ММ-ДД")
            return
        tail_args, after = args[3:], None
        if tail_args and tail_args[0].lower() in ("дистанційно", "вдома", "home"):
            after, tail_args = "home", tail_args[1:]
        elif tail_args and tail_args[0].lower() in ("очно", "школа", "school"):
            after, tail_args = "school", tail_args[1:]
        try:
            holidays.add(start, end, " ".join(tail_args), after)
        except ValueError as e:
            await message.answer(str(e))
            return
        await message.answer(f"✅ Додано канікули {start.isoformat()} – {end.isoformat()}")
    elif sub == "remove" and len(args) >= 2:
        start = _parse_date_arg(args[1])
        if start is None:
            await message.answer("Дата має бути у форматі РРРР-ММ-ДД")
            return
        ok = holidays.remove(start)
        await message.answer("🗑 Канікули прибрано" if ok else "Таких канікул немає (вкажіть дату початку)")
    else:
        await message.answer(
            "Використання:\n/holiday add <від> <до> [дистанційно|очно] <назва>\n"
            "(слово після дат - яким буде тиждень ПІСЛЯ канікул)\n/holiday list\n/holiday remove <дата початку>"
        )


@dp.message(Command("diag"))
async def cmd_diag(message: Message):
    if not _is_allowed(message):
        return
    await message.answer("🔧 Діагностика збереження скасованих уроків:\n\n" + cancellations.diagnostics())


def _parse_hhmm_strict(raw: str) -> str | None:
    """Перевіряє формат ЧЧ:ММ і повертає його ж (нормалізованим) або None, якщо невірний."""
    try:
        h, m = map(int, raw.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return f"{h:02d}:{m:02d}"
    except ValueError:
        return None


@dp.message(Command("settime"))
async def cmd_settime(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    if len(parts) < 3:
        await message.answer("Використання: /settime <номер> <початок> <кінець> [дата РРРР-ММ-ДД]\nПриклад: /settime 3 12:50 13:20")
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /settime 3 12:50 13:20")
        return

    start = _parse_hhmm_strict(parts[1])
    end = _parse_hhmm_strict(parts[2])
    if start is None or end is None:
        await message.answer("Час має бути у форматі ЧЧ:ММ. Приклад: /settime 3 12:50 13:20")
        return

    target_date = _parse_date_arg(parts[3] if len(parts) > 3 else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /settime 3 12:50 13:20 2026-09-10")
        return

    lesson = get_lesson_by_number(number, target_date)
    if lesson is None:
        await message.answer(f"На {target_date.isoformat()} немає {number}-го уроку за розкладом.")
        return

    bell_overrides.set_override(target_date, number, start, end)
    await message.answer(
        f"✅ Час змінено: {number}-й урок «{lesson['name']}» тепер {start}–{end} ({target_date.isoformat()})"
    )


@dp.message(Command("addlesson"))
async def cmd_addlesson(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    if len(parts) < 4:
        await message.answer(
            "Використання: /addlesson <номер> <початок> <кінець> <назва...> [дата РРРР-ММ-ДД]\n"
            "Приклад: /addlesson 0 10:35 11:20 Історія"
        )
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /addlesson 0 10:35 11:20 Історія")
        return

    start = _parse_hhmm_strict(parts[1])
    end = _parse_hhmm_strict(parts[2])
    if start is None or end is None:
        await message.answer("Час має бути у форматі ЧЧ:ММ. Приклад: /addlesson 0 10:35 11:20 Історія")
        return

    # решта - назва, окрім останнього слова, якщо воно схоже на дату РРРР-ММ-ДД
    rest = parts[3:]
    target_date = None
    if rest and _parse_date_arg(rest[-1]) is not None and len(rest[-1]) == 10 and rest[-1].count("-") == 2:
        target_date = _parse_date_arg(rest[-1])
        rest = rest[:-1]
    if target_date is None:
        target_date = _parse_date_arg(None)  # сьогодні

    name = " ".join(rest).strip()
    if not name:
        await message.answer("Вкажіть назву уроку. Приклад: /addlesson 0 10:35 11:20 Історія")
        return

    extra_lessons.add_extra_lesson(target_date, number, name, start, end)
    await message.answer(
        f"✅ Додано: {number}-й урок «{name}», {start}–{end} ({target_date.isoformat()})"
    )


@dp.message(Command("removelesson"))
async def cmd_removelesson(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    if not parts:
        await message.answer("Використання: /removelesson <номер> [дата РРРР-ММ-ДД]")
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /removelesson 0")
        return

    target_date = _parse_date_arg(parts[1] if len(parts) > 1 else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /removelesson 0 2026-09-10")
        return

    removed = extra_lessons.remove_extra_lesson(target_date, number)
    if removed:
        await message.answer(f"🗑 Додатковий {number}-й урок прибрано ({target_date.isoformat()})")
    else:
        await message.answer(f"На {target_date.isoformat()} немає додаткового {number}-го уроку.")


@dp.message(Command("announce"))
async def cmd_announce(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Використання: /announce <текст оголошення>\nПриклад: /announce Завтра урок фізкультури скасовано")
        return

    text = parts[1].strip()
    await bot.send_message(config.CHAT_ID, f"📢 {text}")
    await message.answer("✅ Надіслано в чат")


@dp.message(Command("resettime"))
async def cmd_resettime(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    if not parts:
        await message.answer("Використання: /resettime <номер> [дата РРРР-ММ-ДД]")
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /resettime 3")
        return

    target_date = _parse_date_arg(parts[1] if len(parts) > 1 else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /resettime 3 2026-09-10")
        return

    cleared = bell_overrides.clear_override(target_date, number)
    if cleared:
        await message.answer(f"↩️ Час {number}-го уроку повернуто до стандартного розкладу ({target_date.isoformat()})")
    else:
        await message.answer(f"Для {number}-го уроку на {target_date.isoformat()} і так немає змін часу.")


@dp.message(Command("endnow"))
async def cmd_endnow(message: Message):
    if not _is_allowed(message):
        return

    now = datetime.now(KYIV_TZ)
    today = now.date()
    parts = message.text.split()[1:]

    if parts:
        try:
            number = int(parts[0])
        except ValueError:
            await message.answer("Номер уроку має бути числом. Приклад: /endnow 3 (або просто /endnow - для поточного уроку)")
            return
        lesson = get_lesson_by_number(number, today)
        if lesson is None:
            await message.answer(f"Сьогодні немає {number}-го уроку за розкладом.")
            return
    else:
        current, _, _, _ = find_current_and_next(now)
        if current is None:
            await message.answer("Зараз немає активного уроку. Вкажіть номер вручну: /endnow 3")
            return
        lesson = current

    new_end = now.strftime("%H:%M")
    early_end.mark_early_end(today, lesson["number"], new_end)
    await message.answer(
        f"✅ {lesson['number']}-й урок «{lesson['name']}» позначено завершеним раніше, о {new_end}\n"
        f"(сам розклад дзвінків не змінюється - тільки визначення поточного уроку)"
    )


@dp.message(Command("undoend"))
async def cmd_undoend(message: Message):
    if not _is_allowed(message):
        return

    parts = message.text.split()[1:]
    if not parts:
        await message.answer("Використання: /undoend <номер> [дата РРРР-ММ-ДД]")
        return

    try:
        number = int(parts[0])
    except ValueError:
        await message.answer("Номер уроку має бути числом. Приклад: /undoend 3")
        return

    target_date = _parse_date_arg(parts[1] if len(parts) > 1 else None)
    if target_date is None:
        await message.answer("Невірний формат дати. Приклад: /undoend 3 2026-09-10")
        return

    cleared = early_end.clear_early_end(target_date, number)
    if cleared:
        await message.answer(f"↩️ Позначку про дострокове завершення {number}-го уроку прибрано ({target_date.isoformat()})")
    else:
        await message.answer(f"Для {number}-го уроку на {target_date.isoformat()} і так немає такої позначки.")


# ---------- "Вас відпустили?" - питання адмінам при тривозі ----------

def _alert_dismiss_keyboard(d: date, number: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Так", callback_data=f"ady:{d.isoformat()}:{number}"),
            InlineKeyboardButton(text="Ні", callback_data="adn"),
        ]]
    )


async def ask_admins_if_dismissed(current_lesson: dict, today: date):
    if not config.ADMIN_IDS:
        return
    text = (
        f"🔴 Тривога. Зараз {current_lesson['number']}-й урок «{current_lesson['name']}».\n"
        "Вас відпустили?"
    )
    kb = _alert_dismiss_keyboard(today, current_lesson["number"])
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception as e:
            log.warning("Не вдалося надіслати ЛС адміну %s: %s", admin_id, e)


@dp.callback_query(F.data == "adn")
async def cb_alert_dismiss_no(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer()
        return
    await callback.message.edit_text(callback.message.text + "\n\n➖ Відповідь: ні. Нічого не змінюю.")
    await callback.answer()


@dp.callback_query(F.data.startswith("ady:"))
async def cb_alert_dismiss_yes(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer()
        return

    _, date_str, number_str = callback.data.split(":")
    d = date.fromisoformat(date_str)
    number = int(number_str)

    lesson = get_lesson_by_number(number, d)
    cancellations.cancel_lesson(d, number)
    name = lesson["name"] if lesson else "?"

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Скасовано: {number}-й урок «{name}» ({d.isoformat()})"
    )
    await callback.answer("Урок скасовано")


# ---------- "7/8-й урок сьогодні очно?" - щоденне питання в очний день ----------

def _in_person_keyboard(d: date, number: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Так", callback_data=f"ip:yes:{d.isoformat()}:{number}"),
            InlineKeyboardButton(text="Ні", callback_data=f"ip:no:{d.isoformat()}:{number}"),
        ]]
    )


async def ask_in_person(lesson: dict, d: date):
    if not config.ADMIN_IDS:
        return
    text = f"❓ {lesson['number']}-й урок «{lesson['name']}» сьогодні буде очно чи дистанційно?"
    kb = _in_person_keyboard(d, lesson["number"])
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception as e:
            log.warning("Не вдалося надіслати ЛС адміну %s: %s", admin_id, e)


@dp.callback_query(F.data.startswith("ip:"))
async def cb_in_person(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer()
        return

    _, action, date_str, number_str = callback.data.split(":")
    d = date.fromisoformat(date_str)
    number = int(number_str)
    lesson = get_lesson_by_number(number, d)
    name = lesson["name"] if lesson else "?"

    if action == "yes":
        remote_overrides.mark_in_person(d, number)
        fresh = get_lesson_by_number(number, d)  # перечитуємо, щоб врахувати підтвердження
        await callback.message.edit_text(callback.message.text + "\n\n✅ Так, буде очно.")
        await callback.answer()

        room_part = format_lesson_room(fresh, d) if fresh else ""
        announce = f"📢 Сьогодні {number}-й урок «{name}» буде ОЧНО{room_part}"
        log.info("Оголошую в чаті: %s-й урок сьогодні очно", number)
        await bot.send_message(config.CHAT_ID, announce)
    else:
        await callback.message.edit_text(callback.message.text + "\n\n➖ Ні, залишається дистанційно.")
        await callback.answer()


# ---------- планувальник: розсилки, нагадування, питання ----------
# Усе читає налаштування з settings при кожному проході, тому зміни з
# адмін-панелі діють одразу, без перезапуску.

async def _delete_message_later(chat_id: int | str, message_id: int, when: datetime):
    """Чекає до моменту `when` і видаляє повідомлення (нагадування) з чату."""
    delay = (when - datetime.now(KYIV_TZ)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        log.warning("Не вдалося видалити нагадування message_id=%s: %s", message_id, e)


def _due(now: datetime, hhmm: str | None, window: int = 300) -> bool:
    """Чи настав час hhmm сьогодні (і не минуло більше window секунд - щоб після рестарту не слати запізно)."""
    if not hhmm:
        return False
    h, m = map(int, hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return 0 <= (now - target).total_seconds() < window


async def _tick_morning(now: datetime, fired: dict):
    if _due(now, settings.get("morning_digest_time")) and fired.get("morning") != now.date():
        fired["morning"] = now.date()
        text = format_day_schedule_message("📅 Розклад на сьогодні", now)
        if text is not None:  # у вихідні та канікули уроків немає - не шлемо
            await bot.send_message(config.CHAT_ID, text)


async def _tick_remote_confirm(now: datetime, fired: dict):
    if not (_due(now, settings.get("remote_confirm_time")) and fired.get("remote") != now.date()):
        return
    fired["remote"] = now.date()
    today = now.date()
    if today.weekday() >= 5 or is_school_week(today) is False:
        return
    for number in (7, 8):
        lesson = get_lesson_by_number(number, today)
        if lesson is None or lesson["cancelled"] or remote_overrides.is_in_person(today, number):
            continue
        await ask_in_person(lesson, today)


async def _tick_weektype(now: datetime, fired: dict):
    if now.weekday() != 6:
        return
    if not (_due(now, settings.get("weektype_time")) and fired.get("weektype") != now.date()):
        return
    fired["weektype"] = now.date()
    next_monday = now.date() + timedelta(days=1)
    span = f"{next_monday.strftime('%d.%m')} - {(next_monday + timedelta(days=4)).strftime('%d.%m')}"
    hol = holidays.holiday_on(next_monday)
    if hol:
        text = f"{span} – 🏖 {hol['name']}"
    else:
        school = is_school_week(next_monday)
        if school is None:
            return
        text = f"{span} – {'у школі' if school else 'вдома'}"
    await bot.send_message(config.CHAT_ID, text)


async def _tick_reminders(now: datetime, fired: dict):
    minutes = settings.get("reminder_minutes")
    if not minutes:
        return
    done = fired.setdefault("reminders", set())
    today = now.date()
    for lesson in get_today_lessons(now):
        if lesson["cancelled"]:
            continue
        start_dt = lesson_start_datetime(lesson, today)
        if start_dt is None:
            continue
        remind_at = start_dt - timedelta(minutes=minutes)
        key = (today.isoformat(), lesson["number"], lesson["start"], minutes)
        if key in done or not (remind_at <= now < remind_at + timedelta(seconds=60)):
            continue
        done.add(key)
        if alert_watcher.is_active:  # під час тривоги нагадування не потрібне - після відбою бот сам напише урок
            log.info("Триває тривога - нагадування про %s-й урок пропускаю", lesson["number"])
            continue
        text = (
            f"⏰ За {minutes} хв: {lesson['number']}-й урок «{lesson['name']}», "
            f"{format_lesson_time(lesson, today)}{format_lesson_room(lesson, today)}"
        )
        sent = await bot.send_message(config.CHAT_ID, text)
        asyncio.create_task(_delete_message_later(config.CHAT_ID, sent.message_id, start_dt))


async def scheduler_loop():
    fired: dict = {}
    while True:
        now = datetime.now(KYIV_TZ)
        for tick in (_tick_morning, _tick_remote_confirm, _tick_weektype, _tick_reminders):
            try:
                await tick(now, fired)
            except Exception:
                log.exception("Збій планувальника (%s)", tick.__name__)
        await asyncio.sleep(15)


# ---------- фоновая задача: опрос статуса тревоги ----------

def _level_emoji_and_label(level: str | None) -> tuple[str, str]:
    if level == "red":
        return "🔴", "Повітряна тривога! Червоний рівень небезпеки"
    if level == "yellow":
        return "🟡", "Повітряна тривога! Жовтий рівень небезпеки"
    return "🔴", "Повітряна тривога"  # рівень невідомий - старий формат


def _in_notify_window(now: datetime) -> bool:
    """Чи потрапляє поточний час у діапазон ALERT_NOTIFY_FROM..ALERT_NOTIFY_TO."""
    raw_from = config.ALERT_NOTIFY_FROM
    raw_to = config.ALERT_NOTIFY_TO
    if not raw_from or not raw_to or raw_from.lower() == "off" or raw_to.lower() == "off":
        return True  # обмеження вимкнене
    try:
        fh, fm = map(int, raw_from.split(":"))
        th, tm = map(int, raw_to.split(":"))
    except ValueError:
        return True  # некоректний формат - не обмежуємо

    now_t = now.time()
    start_t = time(fh, fm)
    end_t = time(th, tm)
    return start_t <= now_t <= end_t


async def _try_delete(chat_id: int | str, message_id: int | None):
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        log.warning("Не вдалося видалити старе повідомлення про тривогу: %s", e)


OUTAGE_AFTER_SECONDS = 300  # скільки хвилин без даних, перш ніж написати адмінам


def _threats_line(threats: list[dict]) -> str:
    if not threats or not settings.get("show_threats"):
        return ""
    return "\nЗагрози: " + ", ".join(t["label"] for t in threats)


async def _dm_admins(text: str):
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            log.warning("Не вдалося надіслати ЛС адміну %s: %s", admin_id, e)


async def watch_alerts():
    # id останнього повідомлення "🔴 тривога" і "🟢 відбій" -
    # у чаті лишається тільки одне, найсвіжіше повідомлення про статус.
    last_start_message_id: int | None = None
    last_ended_message_id: int | None = None
    boot = _time.monotonic()
    outage_notified = False

    async with aiohttp.ClientSession() as session:
        while True:
            event = await alert_watcher.poll_once(session)
            now = datetime.now(KYIV_TZ)

            try:  # історія для статистики (самовідновлюється після рестартів)
                alert_history.sync(alert_watcher.is_active, alert_watcher.level, now)
            except Exception:
                log.exception("Не вдалося записати історію тривог")

            # сигнал адмінам, якщо дані про тривоги не надходять (токен, ліміт, мережа)
            age = alert_watcher.data_age_seconds()
            down_for = age if age is not None else int(_time.monotonic() - boot)
            if alert_watcher.last_error and down_for >= OUTAGE_AFTER_SECONDS and not outage_notified:
                outage_notified = True
                await _dm_admins(
                    f"⚠️ Немає даних про тривоги вже {down_for // 60} хв.\n"
                    f"Причина: {alert_watcher.last_error}\n"
                    "Бот може не повідомити про тривогу - стежте за іншими джерелами."
                )
            elif outage_notified and not alert_watcher.last_error:
                outage_notified = False
                await _dm_admins("✅ Дані про тривоги знову надходять.")

            is_weekend = now.weekday() >= 5  # 5=субота, 6=неділя
            should_notify = (
                (config.NOTIFY_ALERTS_ON_WEEKENDS or not is_weekend) and _in_notify_window(now)
            )

            if event == "ended":
                if should_notify and settings.get("notify_ended"):
                    # старе повідомлення "тривога почалась" більше не актуальне
                    await _try_delete(config.CHAT_ID, last_start_message_id)
                    last_start_message_id = None
                    text = format_status_message("🟢 Відбій тривоги")
                    sent = await bot.send_message(config.CHAT_ID, text)
                    last_ended_message_id = sent.message_id
                else:
                    log.info("Відбій тривоги - сповіщення вимкнене/поза розкладом, пропускаю")

            elif event in ("started", "level_changed"):
                if should_notify and settings.get("notify_start"):
                    # старі повідомлення ("відбій" з минулого разу, попередній рівень) вже не актуальні
                    await _try_delete(config.CHAT_ID, last_ended_message_id)
                    last_ended_message_id = None
                    if event == "level_changed":
                        await _try_delete(config.CHAT_ID, last_start_message_id)

                    emoji, label = _level_emoji_and_label(alert_watcher.level)
                    head = "⚠️ Рівень тривоги змінено:\n" if event == "level_changed" else ""
                    sent = await bot.send_message(
                        config.CHAT_ID,
                        f"{head}{emoji} {label}\n«{config.REGION_TITLE}»{_threats_line(alert_watcher.threats)}",
                    )
                    last_start_message_id = sent.message_id
                else:
                    log.info("Початок/зміна рівня тривоги - сповіщення вимкнене/поза розкладом, пропускаю")

            if event == "started" and settings.get("ask_dismiss"):
                current, _, _, _ = find_current_and_next(datetime.now(KYIV_TZ))
                if current is not None:
                    log.info("Питаю адмінів, чи відпустили з %s-го уроку", current["number"])
                    await ask_admins_if_dismissed(current, datetime.now(KYIV_TZ).date())

            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def main():
    asyncio.create_task(watch_alerts())
    asyncio.create_task(scheduler_loop())

    runner = None
    if config.WEBAPP_URL:
        deps = WebAppDeps(
            bot_token=config.BOT_TOKEN,
            chat_id=config.CHAT_ID,
            admin_ids=set(config.ADMIN_IDS),
            bot=bot,
            alert_state_fn=alert_watcher.snapshot,
            region_title=config.REGION_TITLE,
            require_membership=config.WEBAPP_REQUIRE_MEMBERSHIP,
            version=(os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("APP_VERSION") or "dev")[:7],
        )
        runner = await start_server(create_app(deps), "0.0.0.0", config.WEBAPP_PORT)
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Розклад", web_app=WebAppInfo(url=config.WEBAPP_URL))
            )
        except Exception as e:
            log.warning("Не вдалося встановити кнопку меню Mini App: %s", e)

    log.info("Бот запущен, слежу за регионом: %s (Mini App: %s)", config.REGION_TITLE, config.WEBAPP_URL or "вимкнено")
    try:
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
