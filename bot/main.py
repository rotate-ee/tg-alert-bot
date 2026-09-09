"""
Telegram-бот: знает расписание уроков класса и после отбоя воздушной
тревоги пишет в чат "Відбій тривоги" + текущий/следующий урок со временем.

Запуск:
    python bot/main.py
"""
import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import cancellations
import config
from alerts import AlertWatcher
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

alert_watcher = AlertWatcher(token=config.ALERTS_TOKEN, region_title=config.REGION_TITLE)


# ---------- команды для ручной проверки в чате ----------

@dp.message(Command("now"))
async def cmd_now(message: Message):
    text = format_status_message("📚 Розклад")
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
        "/week — розклад на весь тиждень"
    )
    if message.chat.type == "private" and message.from_user.id in config.ADMIN_IDS:
        text += (
            "\n\nКоманди для відмітки скасованих уроків (тільки в ЛС):\n"
            "/cancel <номер> — скасувати урок сьогодні (наприклад /cancel 3)\n"
            "/cancel <номер> <РРРР-ММ-ДД> — скасувати урок на конкретну дату\n"
            "/uncancel <номер> [дата] — повернути урок назад\n"
            "/cancelled [дата] — список скасованих на день\n"
            "/diag — перевірити, чи зберігаються скасування на диску"
        )
    await message.answer(text)


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


@dp.message(Command("diag"))
async def cmd_diag(message: Message):
    if not _is_allowed(message):
        return
    await message.answer("🔧 Діагностика збереження скасованих уроків:\n\n" + cancellations.diagnostics())


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


# ---------- фоновая задача: ранкова розсилка розкладу ----------

def _parse_digest_time() -> tuple[int, int] | None:
    raw = config.MORNING_DIGEST_TIME
    if not raw or raw.lower() == "off":
        return None
    try:
        h, m = map(int, raw.split(":"))
        return h, m
    except ValueError:
        log.warning("Некоректний MORNING_DIGEST_TIME=%r, розсилку вимкнено", raw)
        return None


async def morning_digest():
    parsed = _parse_digest_time()
    if parsed is None:
        log.info("Ранкова розсилка вимкнена (MORNING_DIGEST_TIME=%r)", config.MORNING_DIGEST_TIME)
        return

    hour, minute = parsed
    while True:
        now = datetime.now(KYIV_TZ)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        text = format_day_schedule_message("📅 Розклад на сьогодні")
        if text is not None:
            log.info("Відправляю ранковий розклад")
            await bot.send_message(config.CHAT_ID, text)
        else:
            log.info("Сьогодні уроків немає (вихідний) - ранкову розсилку пропускаю")


# ---------- фоновая задача: нагадування за N хвилин до уроку ----------

async def _delete_message_later(chat_id: int | str, message_id: int, when: datetime):
    """Чекає до моменту `when` і видаляє повідомлення (нагадування) з чату."""
    delay = (when - datetime.now(KYIV_TZ)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
        log.info("Видалив нагадування (message_id=%s) - урок почався", message_id)
    except Exception as e:
        log.warning("Не вдалося видалити нагадування message_id=%s: %s", message_id, e)


async def lesson_reminders():
    if config.LESSON_REMINDER_MINUTES is None:
        log.info("Нагадування про уроки вимкнені (LESSON_REMINDER_MINUTES=off)")
        return

    minutes_before = config.LESSON_REMINDER_MINUTES

    while True:
        now = datetime.now(KYIV_TZ)
        today = now.date()
        pending = []
        for lesson in get_today_lessons(now):
            if lesson["cancelled"]:
                continue
            start_dt = lesson_start_datetime(lesson, today)
            if start_dt is None:
                continue
            reminder_dt = start_dt - timedelta(minutes=minutes_before)
            if reminder_dt > now:
                pending.append((reminder_dt, lesson, start_dt))

        if not pending:
            # на сьогодні нагадувати більше нічого - спимо до завтрашнього ранку
            tomorrow = datetime.combine(today + timedelta(days=1), time(0, 1), tzinfo=KYIV_TZ)
            await asyncio.sleep((tomorrow - now).total_seconds())
            continue

        pending.sort(key=lambda triple: triple[0])
        reminder_dt, lesson, start_dt = pending[0]
        await asyncio.sleep((reminder_dt - now).total_seconds())

        # перевіряємо ще раз прямо перед відправкою - раптом урок щойно скасували
        # або якраз зараз триває тривога (тоді нагадування не потрібне - є учні
        # в укритті, після відбою бот сам напише, який урок зараз)
        fresh = get_lesson_by_number(lesson["number"], today)
        if alert_watcher.is_active:
            log.info("Зараз триває тривога - нагадування про %s-й урок пропускаю", lesson["number"])
        elif fresh and not fresh["cancelled"]:
            text = (
                f"⏰ За {minutes_before} хв: {fresh['number']}-й урок «{fresh['name']}», "
                f"{format_lesson_time(fresh)}{format_lesson_room(fresh, today)}"
            )
            log.info("Надсилаю нагадування про %s-й урок", fresh["number"])
            sent = await bot.send_message(config.CHAT_ID, text)
            # видалимо це повідомлення, як тільки урок почнеться
            asyncio.create_task(_delete_message_later(config.CHAT_ID, sent.message_id, start_dt))
        else:
            log.info("Урок %s скасовано, нагадування пропускаю", lesson["number"])
        # цикл повториться і сам перерахує наступне найближче нагадування


# ---------- фоновая задача: неділя ввечері - тип наступного тижня ----------

def _parse_hhmm(raw: str) -> tuple[int, int] | None:
    if not raw or raw.lower() == "off":
        return None
    try:
        h, m = map(int, raw.split(":"))
        return h, m
    except ValueError:
        return None


async def week_type_sunday_reminder():
    parsed = _parse_hhmm(config.WEEK_TYPE_REMINDER_TIME)
    if parsed is None:
        log.info("Недільне нагадування про тип тижня вимкнене")
        return

    hour, minute = parsed
    while True:
        now = datetime.now(KYIV_TZ)
        days_until_sunday = (6 - now.weekday()) % 7
        target = (now + timedelta(days=days_until_sunday)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=7)
        await asyncio.sleep((target - now).total_seconds())

        next_monday = target.date() + timedelta(days=1)
        school = is_school_week(next_monday)
        if school is None:
            log.info("SCHOOL_WEEK_ANCHOR_MONDAY не налаштовано - недільне нагадування пропускаю")
            continue

        friday = next_monday + timedelta(days=4)
        week_word = "у школі" if school else "вдома"
        text = f"{next_monday.strftime('%d.%m')} - {friday.strftime('%d.%m')} – {week_word}"

        log.info("Надсилаю недільне нагадування про тип тижня в чат")
        await bot.send_message(config.CHAT_ID, text)


# ---------- фоновая задача: опрос статуса тревоги ----------

async def watch_alerts():
    async with aiohttp.ClientSession() as session:
        while True:
            event = await alert_watcher.poll_once(session)
            is_weekend = datetime.now(KYIV_TZ).weekday() >= 5  # 5=субота, 6=неділя
            should_notify = config.NOTIFY_ALERTS_ON_WEEKENDS or not is_weekend

            if event == "ended":
                if should_notify:
                    text = format_status_message("🟢 Відбій тривоги")
                    log.info("Отбой тревоги, отправляю сообщение")
                    await bot.send_message(config.CHAT_ID, text)
                else:
                    log.info("Отбой тревоги у вихідний - сповіщення пропускаю")

            elif event == "started" and config.NOTIFY_ON_START:
                if should_notify:
                    log.info("Тревога началась, отправляю уведомление")
                    await bot.send_message(
                        config.CHAT_ID,
                        f"🔴 Повітряна тривога в «{config.REGION_TITLE}»!",
                    )
                else:
                    log.info("Тривога почалась у вихідний - сповіщення пропускаю")

            if event == "started":
                current, _, _, _ = find_current_and_next(datetime.now(KYIV_TZ))
                if current is not None:
                    log.info("Питаю адмінів, чи відпустили з %s-го уроку", current["number"])
                    await ask_admins_if_dismissed(current, datetime.now(KYIV_TZ).date())

            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def main():
    asyncio.create_task(watch_alerts())
    asyncio.create_task(morning_digest())
    asyncio.create_task(lesson_reminders())
    asyncio.create_task(week_type_sunday_reminder())
    log.info("Бот запущен, слежу за регионом: %s", config.REGION_TITLE)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
