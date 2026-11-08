import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiohttp.test_utils import TestClient, TestServer

import api
import broadcast
import notify
import users
from alerts import AlertWatcher

K = ZoneInfo("Europe/Kyiv")


def tg_user(uid, name="Оля", username="olya"):
    return {"id": uid, "first_name": name, "username": username}


class FakeBot:
    def __init__(self, blocked=()):
        self.sent, self.blocked = [], set(blocked)

    async def send_message(self, uid, text, parse_mode=None, reply_markup=None):
        if uid in self.blocked:
            raise TelegramForbiddenError(method=SendMessage(chat_id=uid, text=text), message="Forbidden: bot was blocked by the user")
        self.sent.append((uid, text, reply_markup))

    async def get_me(self):
        class Me:
            username = "test_bot"
        return Me()


@pytest.fixture(autouse=True)
def release_broadcast_lock():
    broadcast._running = False
    yield
    broadcast._running = False


def test_record_start_counts_new_and_returning_users():
    assert users.record_start(tg_user(1)) is True
    assert users.record_start(tg_user(1)) is False        # повторний /start - не новий
    assert users.record_start(tg_user(2, "Іван", "")) is True
    t = users.stats()["totals"]
    assert (t["started"], t["starts_total"], t["starts_7"], t["new_7"], t["blocked"]) == (2, 3, 3, 2, 0)
    today = users.stats()["by_day"][-1]
    assert (today["starts"], today["new"]) == (3, 2) and len(users.stats()["by_day"]) == 30
    assert users.stats()["recent"][0]["name"] == "Іван"


def test_blocked_users_leave_the_audience_and_return_on_start():
    users.record_start(tg_user(1)); users.record_start(tg_user(2))
    users.set_blocked(2, True)
    assert users.audience_ids("all") == [1] and users.stats()["totals"]["blocked"] == 1
    users.record_start(tg_user(2))                         # повернувся і знову натиснув Start
    assert sorted(users.audience_ids("all")) == [1, 2]


def test_audience_subscribers_is_subset():
    users.record_start(tg_user(1)); users.record_start(tg_user(2)); users.record_start(tg_user(3))
    notify.set_topics(2, ["changes"])
    assert users.audience_ids("subscribers") == [2]
    assert sorted(users.audience_ids("all")) == [1, 2, 3]


def test_seed_from_activity_runs_once():
    import activity
    activity.record({"id": 77, "first_name": "Марк"}, "sch")
    assert users.seed_from_activity() == 1 and users.seed_from_activity() == 0
    assert users.audience_ids("all") == [77] and users.stats()["totals"]["started"] == 0


def test_broadcast_marks_blocked_logs_history_and_reports_to_admin():
    for uid in (1, 2, 3):
        users.record_start(tg_user(uid))
    bot = FakeBot(blocked={2})
    assert broadcast.try_begin() is True and broadcast.try_begin() is False       # лише одна розсилка одночасно
    result = asyncio.run(broadcast.run(bot, 42, "Завтра зміни", "all", "https://app.example/"))
    assert (result["sent"], result["blocked"], result["total"]) == (2, 1, 3)
    assert users.audience_ids("all") == [1, 3]                                    # заблокованого більше не чіпаємо
    assert [u for u, _, _ in bot.sent if u != 42] == [1, 3]
    assert bot.sent[0][2].inline_keyboard[0][0].web_app.url == "https://app.example/"
    assert "Доставлено: 2 з 3" in bot.sent[-1][1] and bot.sent[-1][0] == 42       # звіт адміну
    h = broadcast.history(1)[0]
    assert (h["sent"], h["blocked"], h["text"], h["audience"]) == (2, 1, "Завтра зміни", "all")
    assert broadcast.is_running() is False


def test_test_broadcast_goes_only_to_admin_and_is_not_logged():
    users.record_start(tg_user(1))
    bot = FakeBot()
    broadcast.try_begin()
    result = asyncio.run(broadcast.run(bot, 42, "тест", "all", test=True))
    assert result["total"] == 1 and [u for u, _, _ in bot.sent] == [42] and broadcast.history() == []


def run_api(scenario, bot=None):
    bot = bot or FakeBot()

    async def go():
        client = TestClient(TestServer(api.build_app(bot, AlertWatcher("x", "M"))))
        await client.start_server()
        try:
            return await scenario(client, bot)
        finally:
            await client.close()
    return asyncio.run(go())


def test_admin_users_endpoint_and_permissions(admin_headers, user_headers):
    users.record_start(tg_user(1))

    async def f(c, bot):
        assert (await c.get("/api/admin/users")).status == 403
        assert (await c.get("/api/admin/users", headers=user_headers)).status == 403
        j = await (await c.get("/api/admin/users", headers=admin_headers)).json()
        assert j["stats"]["totals"]["started"] == 1 and j["audience"] == {"all": 1, "subscribers": 0} and j["running"] is False
    run_api(f)


def test_broadcast_endpoint_validation_and_flow(admin_headers, user_headers):
    users.record_start(tg_user(1)); users.record_start(tg_user(2))
    api.MINIAPP_URL_FN = lambda: "https://app.example/"

    async def f(c, bot):
        post = lambda body, h=admin_headers: c.post("/api/admin/broadcast", json=body, headers=h)
        assert (await post({"text": "x"}, user_headers)).status == 403
        assert (await post({"text": "   "})).status == 400
        assert (await post({"text": "x" * 3501})).status == 400
        assert (await post({"text": "x", "audience": "everyone"})).status == 400
        assert (await post({"text": "x", "audience": "subscribers"})).status == 400      # порожня аудиторія
        r = await post({"text": "Привіт", "test": True, "button": True})
        assert r.status == 200 and [u for u, _, _ in bot.sent] == [42]                    # тест лише адміну
        r = await post({"text": "Привіт усім", "button": True})
        assert r.status == 200 and (await r.json())["total"] == 2
        for _ in range(100):
            if not broadcast.is_running():
                break
            await asyncio.sleep(0.05)
        assert sorted(u for u, t, _ in bot.sent if t == "Привіт усім") == [1, 2]
        assert broadcast.history(1)[0]["sent"] == 2
    run_api(f)


def test_broadcast_rejects_second_run_while_busy(admin_headers):
    users.record_start(tg_user(1))

    async def f(c, bot):
        broadcast.try_begin()                                                             # імітуємо розсилку, що вже триває
        r = await c.post("/api/admin/broadcast", json={"text": "x"}, headers=admin_headers)
        assert r.status == 409 and (await r.json())["error"] == "busy"
    run_api(f)


def test_test_broadcast_reports_when_admin_never_started_bot(admin_headers):
    async def f(c, bot):
        r = await c.post("/api/admin/broadcast", json={"text": "x", "test": True}, headers=admin_headers)
        assert r.status == 409 and (await r.json())["error"] == "start_bot"
    run_api(f, bot=FakeBot(blocked={42}))
