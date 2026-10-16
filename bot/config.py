import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. "
            f"Проверьте файл .env (см. .env.example)."
        )
    return value


BOT_TOKEN = _require("BOT_TOKEN")
CHAT_ID = _require("CHAT_ID")  # чат/канал/группа, куда бот шлёт "отбой"
ALERTS_TOKEN = _require("ALERTS_TOKEN")  # токен alerts.in.ua

# Название области ровно так, как оно фигурирует в alerts.in.ua,
# например: "Миколаївська область"
REGION_TITLE = os.getenv("REGION_TITLE", "Миколаївська область")

# Как часто опрашивать API тревог, секунд.
# alerts.in.ua просит не чаще 1 запроса в 5-10 сек на бесплатном токене.
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

# Отправлять ли сообщение и при НАЧАЛЕ тревоги (не только при отбое)
NOTIFY_ON_START = os.getenv("NOTIFY_ON_START", "true").lower() == "true"

# Какая "група тижнів" сейчас действует для предметов, что чередуются
# по неделям месяца ("Мистецтво/Музика", "Історія/Гром.освіта" и т.п.):
#   "auto" - считать автоматически по числу месяца (1-2 неделя / 3-4 неделя)
#   "1_2"  - всегда считать, что сейчас 1-й или 2-й тиждень місяця
#   "3_4"  - всегда считать, что сейчас 3-й или 4-й тиждень місяця
# Если авторасчёт не совпадает с реальным журналом школы - задайте вручную.
WEEK_GROUP_OVERRIDE = os.getenv("WEEK_GROUP_OVERRIDE", "auto")

# Telegram user_id тех, кому разрешено отмечать уроки отменёнными
# (команды /cancel, /uncancel, /cancelled - работают только в ЛС с ботом).
# Несколько ID через запятую: ADMIN_IDS=111111111,222222222
# Узнать свой user_id можно у @userinfobot.
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
}

# Во сколько (за часом Europe/Kyiv, формат ЧЧ:ММ) присылать в чат
# повний розклад на сьогодні. Порожньо або "off" - вимкнути розсилку.
MORNING_DIGEST_TIME = os.getenv("MORNING_DIGEST_TIME", "10:00").strip()

# Дата (РРРР-ММ-ДД) якогось понеділка, коли клас навчався ОЧНО в школі.
# Далі тижні чергуються сами: наступний тиждень - вдома, через тиждень -
# знову в школі, і т.д. Порожньо - ознака "школа/вдома" ніде не показується.
SCHOOL_WEEK_ANCHOR_MONDAY = os.getenv("SCHOOL_WEEK_ANCHOR_MONDAY", "").strip()

# Чи слати повідомлення про початок/відбій тривоги в суботу і неділю.
# За замовчуванням - ні (моніторинг у фоні продовжується, просто без
# сповіщень у чат). true - слати як завжди, і у вихідні теж.
NOTIFY_ALERTS_ON_WEEKENDS = os.getenv("NOTIFY_ALERTS_ON_WEEKENDS", "true").strip().lower() == "true"

# Часовий діапазон (Europe/Kyiv, ЧЧ:ММ), коли слати сповіщення про
# тривогу/відбій у чат. Поза цим діапазоном бот мовчить (моніторинг у
# фоні продовжується). Порожньо або "off" в обох полях - без обмежень.
ALERT_NOTIFY_FROM = os.getenv("ALERT_NOTIFY_FROM", "off").strip()
ALERT_NOTIFY_TO = os.getenv("ALERT_NOTIFY_TO", "off").strip()

# Во сколько (Europe/Kyiv, ЧЧ:ММ) в очний день питати адміна, чи 7-й
# і 8-й уроки будуть очно (замість дистанційних за замовчуванням).
REMOTE_CONFIRM_TIME = os.getenv("REMOTE_CONFIRM_TIME", "12:00").strip()

# За скільки хвилин до початку наступного уроку слати коротке
# нагадування в чат. "off" або "0" - вимкнути нагадування.
_reminder_raw = os.getenv("LESSON_REMINDER_MINUTES", "5").strip()
LESSON_REMINDER_MINUTES = None if _reminder_raw.lower() in ("off", "0", "") else int(_reminder_raw)

# Во сколько (Europe/Kyiv, ЧЧ:ММ) у неділю ввечері слати окреме
# повідомлення адмінам про тип наступного тижня (школа/вдома).
# Працює тільки якщо налаштовано SCHOOL_WEEK_ANCHOR_MONDAY. "off" - вимкнути.
WEEK_TYPE_REMINDER_TIME = os.getenv("WEEK_TYPE_REMINDER_TIME", "19:00").strip()

# ---- Mini App (веб-застосунок усередині Telegram) ----
# Публічна HTTPS-адреса, за якою доступний бот (її дає хостинг, напр.
# https://my-bot.up.railway.app). Порожньо - Mini App вимкнено.
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
# Порт вбудованого веб-сервера (Railway/Fly самі задають змінну PORT).
WEBAPP_PORT = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT") or "8080")
# Пускати в застосунок тільки учасників CHAT_ID (адміни - завжди).
WEBAPP_REQUIRE_MEMBERSHIP = os.getenv("WEBAPP_REQUIRE_MEMBERSHIP", "true").strip().lower() == "true"
