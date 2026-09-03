"""
Telegram-бот: знает расписание уроков класса и после отбоя воздушной
тревоги пишет в чат "Відбій тривоги" + текущий/следующий урок со временем.

Запуск:
    python bot/main.py
"""
import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import config
from alerts import AlertWatcher
from lessons import format_status_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


# ---------- команды для ручной проверки в чате ----------

@dp.message(Command("now"))
async def cmd_now(message: Message):
    text = format_status_message("📚 Розклад")
    await message.answer(text)


@dp.message(Command("start", "help"))
async def cmd_help(message: Message):
    await message.answer(
        "Привіт! Я стежу за повітряною тривогою в "
        f"«{config.REGION_TITLE}» і після відбою пишу сюди, "
        "який зараз урок.\n\n"
        "Команди:\n"
        "/now — показати поточний/наступний урок прямо зараз"
    )


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
    log.info("Бот запущен, слежу за регионом: %s", config.REGION_TITLE)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
