"""
Telegram-бот: знает расписание уроков класса и после отбоя воздушной
тревоги пишет в чат "Відбій тривоги" + текущий/следующий урок со временем.

Запуск:
    python bot/main.py
"""
import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import cancellations
import config
from alerts import AlertWatcher
from lessons import format_day_schedule_message, format_status_message, get_lesson_by_number

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


# ---------- команды для ручной проверки в чате ----------

@dp.message(Command("now"))
async def cmd_now(message: Message):
    text = format_status_message("📚 Розклад")
    await message.answer(text)


@dp.message(Command("start", "help"))
async def cmd_help(message: Message):
    text = (
        "Привіт! Я стежу за повітряною тривогою в "
        f"«{config.REGION_TITLE}» і після відбою пишу сюди, "
        "який зараз урок.\n\n"
        "Команди:\n"
        "/now — показати поточний/наступний урок прямо зараз"
    )
    if message.chat.type == "private" and message.from_user.id in config.ADMIN_IDS:
        text += (
            "\n\nКоманди для відмітки скасованих уроків (тільки в ЛС):\n"
            "/cancel <номер> — скасувати урок сьогодні (наприклад /cancel 3)\n"
            "/cancel <номер> <РРРР-ММ-ДД> — скасувати урок на конкретну дату\n"
            "/uncancel <номер> [дата] — повернути урок назад\n"
            "/cancelled [дата] — список скасованих на день"
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


# ---------- фоновая задача: опрос статуса тревоги ----------

async def watch_alerts():
    watcher = AlertWatcher(token=config.ALERTS_TOKEN, region_title=config.REGION_TITLE)

    async with aiohttp.ClientSession() as session:
        while True:
            event = await watcher.poll_once(session)

            if event == "ended":
                text = format_status_message("🟢 Відбій тривоги")
                log.info("Отбой тревоги, отправляю сообщение")
                await bot.send_message(config.CHAT_ID, text)

            elif event == "started" and config.NOTIFY_ON_START:
                log.info("Тревога началась, отправляю уведомление")
                await bot.send_message(
                    config.CHAT_ID,
                    f"🔴 Повітряна тривога в «{config.REGION_TITLE}»!",
                )

            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def main():
    asyncio.create_task(watch_alerts())
    asyncio.create_task(morning_digest())
    log.info("Бот запущен, слежу за регионом: %s", config.REGION_TITLE)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
