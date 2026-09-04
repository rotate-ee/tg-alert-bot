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
