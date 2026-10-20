"""
Локальний перегляд Mini App БЕЗ Telegram, токенів і хостингу.

    python tools/preview.py                       # типовий перегляд
    python tools/preview.py --week home           # тиждень вдома
    python tools/preview.py --alert red           # червона тривога зараз (yellow / red / none)
    python tools/preview.py --time "2026-10-15 12:40"   # удавати, що зараз саме цей час
    python tools/preview.py --host 0.0.0.0        # відкрити з телефона в тій самій Wi-Fi мережі
    python tools/preview.py --as-member           # переглянути як звичайний учень (без адмін-вкладки)

Потім відкрийте http://localhost:8080 у браузері. Дані тимчасові - реальні
файли бота не чіпаються, усі "оголошення в чат" просто друкуються тут.
"""
import argparse
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
KYIV = ZoneInfo("Europe/Kyiv")

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8080)
ap.add_argument("--week", choices=["school", "home"], default="school")
ap.add_argument("--alert", choices=["none", "yellow", "red"], default="yellow")
ap.add_argument("--time", default=None, help='напр. "2026-10-15 12:40" (за замовчуванням - реальний час)')
ap.add_argument("--as-member", action="store_true", help="не адмін")
ap.add_argument("--stale", action="store_true", help="показати попередження \"дані про тривоги застаріли\"")
args = ap.parse_args()

fake_start = datetime.strptime(args.time, "%Y-%m-%d %H:%M").replace(tzinfo=KYIV) if args.time else datetime.now(KYIV)
monday = (fake_start - timedelta(days=fake_start.weekday())).date()
anchor = monday if args.week == "school" else monday - timedelta(days=7)

# Усе налаштовуємо ДО імпорту модулів бота (вони читають змінні при імпорті)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tgbot-preview-")
os.environ.setdefault("BOT_TOKEN", "123456789:AAdummydummydummydummydummydummydu")
os.environ.setdefault("CHAT_ID", "-1001")
os.environ.setdefault("ALERTS_TOKEN", "preview")
os.environ["SCHOOL_WEEK_ANCHOR_MONDAY"] = anchor.isoformat()
sys.path.insert(0, str(ROOT / "bot"))

import alert_history  # noqa: E402
import cancellations  # noqa: E402
import extra_lessons  # noqa: E402
import remote_overrides  # noqa: E402
from lessons import get_lessons_for_date  # noqa: E402
from webapp import WebAppDeps, create_app, start_server  # noqa: E402


class FakeBot:
    async def send_message(self, chat_id, text, **kw):
        print(f"  → у чат {chat_id}: {text}")


def seed():
    today = fake_start.date()
    lessons = get_lessons_for_date(today)
    nums = [l["number"] for l in lessons]
    if 3 in nums:
        cancellations.cancel_lesson(today, 3)
    if 7 in nums and args.week == "school":
        remote_overrides.mark_in_person(today, 7)
    extra_lessons.add_extra_lesson(today, 0, "Історія", "10:35", "11:20")

    # трохи історії тривог для вкладки "Тривоги"
    base = fake_start - timedelta(days=2, hours=3)
    alert_history.sync(True, "yellow", base)
    alert_history.sync(True, "red", base + timedelta(minutes=9))
    alert_history.sync(False, None, base + timedelta(minutes=47))
    base2 = fake_start - timedelta(days=1, hours=1)
    alert_history.sync(True, "yellow", base2)
    alert_history.sync(False, None, base2 + timedelta(minutes=22))
    if args.alert != "none":
        alert_history.sync(True, args.alert, fake_start - timedelta(minutes=12))


def alert_snapshot() -> dict:
    active = args.alert != "none"
    threats = []
    if active:
        threats = [{"type": "drones", "label": "Дрони", "level": "yellow"}]
        if args.alert == "red":
            threats.append({"type": "ballistic_missiles", "label": "Балістика", "level": "red"})
    return {
        "active": active, "level": args.alert if active else None, "threats": threats, "notes": None,
        "data_age_seconds": 400 if args.stale else 4, "stale": args.stale,
        "last_error": "HTTP 429 - перевищено ліміт запитів" if args.stale else None,
        "nearby": [
            {"title": "Миколаївський район", "type": "raion", "level": "yellow"},
            {"title": "Херсонська область", "type": "oblast", "level": "red"},
            {"title": "Одеська область", "type": "oblast", "level": "yellow"},
        ],
    }


def now_fn():
    return fake_start + timedelta(seconds=time.monotonic() - t0)


async def main():
    seed()
    deps = WebAppDeps(
        bot_token=os.environ["BOT_TOKEN"], chat_id=os.environ["CHAT_ID"],
        admin_ids={1}, bot=FakeBot(),
        alert_state_fn=alert_snapshot,
        now_fn=now_fn, region_title="Вознесенський район", require_membership=False,
        auth_fn=lambda raw: {"id": 2 if args.as_member else 1, "first_name": "Демо"},  # ТІЛЬКИ для перегляду
    )
    await start_server(create_app(deps), args.host, args.port)
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"\n  Mini App (демо) працює: http://{shown}:{args.port}")
    print(f"  Тиждень: {'очний' if args.week == 'school' else 'вдома'} | тривога: {args.alert} | зараз: {fake_start:%d.%m %H:%M}")
    print("  Зупинити: Ctrl+C\n")
    await asyncio.Event().wait()


t0 = time.monotonic()
try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
