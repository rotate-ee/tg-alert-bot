import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiohttp.test_utils import TestClient, TestServer

import alert_log
import api
import notify
from alerts import AlertWatcher

K = ZoneInfo("Europe/Kyiv")


class FakeBot:
    def __init__(self, can_write=True):
        self.can_write, self.messages, self.documents = can_write, [], []

    def _check(self, uid):
        if not self.can_write:
            raise TelegramForbiddenError(method=SendMessage(chat_id=uid, text="x"), message="Forbidden: bot can't initiate conversation with a user")

    async def send_message(self, uid, text, parse_mode=None):
        self._check(uid)
        self.messages.append((uid, text))

    async def send_document(self, uid, document, caption=None):
        self._check(uid)
        self.documents.append((uid, document.filename, caption))

    async def get_me(self):
        class Me:
            username = "test_bot"
        return Me()


def run(scenario, bot=None, watcher=None):
    """Піднімає API у тестовому режимі, виконує сценарій async def f(client, bot, watcher)."""
    bot, watcher = bot or FakeBot(), watcher or AlertWatcher("x", "Миколаївська область")

    async def go():
        client = TestClient(TestServer(api.build_app(bot, watcher)))
        await client.start_server()
        try:
            return await scenario(client, bot, watcher)
        finally:
            await client.close()
    return asyncio.run(go())


def test_state_is_public_and_admin_flag(admin_headers, user_headers):
    async def f(c, bot, w):
        assert (await c.get("/api/state?days=1")).status == 200
        assert (await (await c.get("/api/me", headers=admin_headers)).json())["admin"] is True
        me_user = await (await c.get("/api/me", headers=user_headers)).json()
        assert me_user["admin"] is False and me_user["subscription"] == [] and me_user["bot_username"] == "test_bot"
        assert (await (await c.get("/api/me")).json()) == {"admin": False}
    run(f)


def test_admin_endpoints_require_admin(admin_headers, user_headers):
    async def f(c, bot, w):
        body = {"date": "2026-10-19", "number": 1}
        assert (await c.post("/api/admin/cancel", json=body)).status == 403
        assert (await c.post("/api/admin/cancel", json=body, headers=user_headers)).status == 403
        assert (await c.post("/api/admin/cancel", json=body, headers=admin_headers)).status == 200
        assert (await c.post("/api/admin/backup/send", headers=user_headers)).status == 403
        assert (await c.post("/api/admin/alert/clear", json={"month": "2026-09"}, headers=user_headers)).status == 403
    run(f)


def test_stale_flag_when_alert_data_is_old(admin_headers):
    async def f(c, bot, w):
        fresh = (await (await c.get("/api/state?days=1", headers=admin_headers)).json())["alert"]
        assert fresh["stale"] is False
        w.last_ok = datetime.now(K) - timedelta(minutes=10)
        w.last_error = "TimeoutError: boom"
        stale = (await (await c.get("/api/state?days=1", headers=admin_headers)).json())["alert"]
        assert stale["stale"] is True and stale["stale_minutes"] >= 10 and stale["last_error"] == "TimeoutError: boom"
        public = (await (await c.get("/api/state?days=1")).json())["alert"]
        assert public["stale"] is True and "last_error" not in public  # причину бачать лише адміни
    run(f)


def test_history_contains_lesson_hits(admin_headers):
    import lessons
    monday = date(2026, 10, 19)
    lesson = next(l for l in lessons.get_lessons_for_date(monday) if not l["cancelled"])
    h, m = map(int, lesson["start"].split(":"))
    start = datetime(2026, 10, 19, h, m, tzinfo=K) + timedelta(minutes=5)
    alert_log.add(start.strftime("%Y-%m-%d %H:%M"), (start + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M"), "red")

    async def f(c, bot, w):
        hist = (await (await c.get("/api/state?days=1", headers=admin_headers)).json())["alert"]["history"]
        assert [x["number"] for x in hist[0]["hit"]] == [lesson["number"]]
        stats = await (await c.get("/api/alert_stats")).json()
        assert stats["totals"]["count"] == 1 and stats["lessons"]["alerts_with_hits"] == 1
    run(f)


def test_alert_edit_rules_over_http(admin_headers):
    entry = alert_log.add("2026-10-30 08:00", "2026-10-30 08:20", "red")

    async def f(c, bot, w):
        post = lambda path, body, h=admin_headers: c.post("/api/admin/alert/" + path, json=body, headers=h)
        assert (await post("edit", {"id": entry["id"], "end": "2026-10-30 07:00"})).status == 400
        assert (await post("edit", {"id": entry["id"], "level": "yellow", "is_false": True})).status == 200
        assert alert_log.get(entry["id"])["is_false"] is True
        alert_log.on_started("red")
        w._was_active, w._level = True, "red"
        ongoing = alert_log.all_entries()[-1]["id"]
        assert (await post("delete", {"id": ongoing})).status == 400            # поточну тривогу не видалити
        assert (await post("edit", {"id": ongoing, "end": "2026-11-01 10:00"})).status == 400
        assert (await post("edit", {"id": ongoing, "level": "yellow"})).status == 200
        assert (await post("clear", {"month": ongoing and alert_log.get(ongoing)["start"][:7]})).status == 200
        assert alert_log.get(ongoing) is not None                               # при очищенні місяця поточна лишилась
    run(f)


def test_subscribe_flow(user_headers):
    async def f(c, bot, w):
        assert (await c.post("/api/subscribe", json={"topics": ["alerts"]})).status == 403
        r = await c.post("/api/subscribe", json={"topics": ["alerts", "changes", "bogus"]}, headers=user_headers)
        assert r.status == 200 and (await r.json())["topics"] == ["alerts", "changes"]
        assert notify.get_topics(7) == ["alerts", "changes"] and bot.messages and bot.messages[0][0] == 7
        assert (await c.post("/api/subscribe", json={"topics": "alerts"}, headers=user_headers)).status == 400
        r = await c.post("/api/subscribe", json={"topics": []}, headers=user_headers)
        assert (await r.json())["topics"] == [] and notify.get_topics(7) == []
    run(f)


def test_subscribe_requires_started_bot(user_headers):
    async def f(c, bot, w):
        r = await c.post("/api/subscribe", json={"topics": ["alerts"]}, headers=user_headers)
        assert r.status == 409 and (await r.json())["error"] == "start_bot"
        assert notify.get_topics(7) == []   # без можливості писати - підписку не зберігаємо
    run(f, bot=FakeBot(can_write=False))


def test_schedule_changes_are_recorded_for_notifications(admin_headers):
    soon = (datetime.now(K).date() + timedelta(days=1)).isoformat()
    far = (datetime.now(K).date() + timedelta(days=60)).isoformat()

    async def f(c, bot, w):
        await c.post("/api/admin/cancel", json={"date": soon, "number": 2}, headers=admin_headers)
        await c.post("/api/admin/time", json={"date": soon, "number": 3, "start": "14:00", "end": "14:45"}, headers=admin_headers)
        await c.post("/api/admin/cancel", json={"date": far, "number": 2}, headers=admin_headers)     # надто далеко - без сповіщення
        await c.post("/api/admin/cancel", json={"date": soon, "number": 2}, headers=None or {})       # без прав - не записується
        await c.post("/api/admin/holiday", json={"from": "2026-12-24", "to": "2027-01-10", "title": "Зимові"}, headers=admin_headers)
        due = {d: lines for d, lines, _ in notify.due_changes(quiet_seconds=0)}
        assert set(due) == {soon, "2026-12-24"}
        assert any("скасовано" in x for x in due[soon]) and any("14:00" in x for x in due[soon])
        assert "Зимові" in due["2026-12-24"][0]
    run(f)


def test_backup_send_to_admin_and_start_bot_error(admin_headers):
    async def ok(c, bot, w):
        r = await c.post("/api/admin/backup/send", headers=admin_headers)
        assert r.status == 200
        assert bot.documents and bot.documents[0][0] == 42 and bot.documents[0][1].startswith("backup_")
    run(ok)

    async def blocked(c, bot, w):
        assert (await c.post("/api/admin/backup/send", headers=admin_headers)).status == 409
    run(blocked, bot=FakeBot(can_write=False))
