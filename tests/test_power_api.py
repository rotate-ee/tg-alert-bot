import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

import api
import notify  # noqa: F401  (імпорт гарантує, що модулі завантажені як у бота)
import power
import settings
from alerts import AlertWatcher

K = ZoneInfo("Europe/Kyiv")
FIXTURE = (Path(__file__).parent / "fixtures" / "dpem_index.html").read_text(encoding="utf-8")


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None, disable_notification=False, **kw):
        self.sent.append((chat_id, text, disable_notification))


@pytest.fixture(autouse=True)
def reset_power_state():
    power.last_ok = None
    power.last_error = None
    power.fail_count = 0
    yield
    power.NOTIFIER = None


def run_api(scenario):
    async def go():
        client = TestClient(TestServer(api.build_app(FakeBot(), AlertWatcher("x", "M"))))
        await client.start_server()
        try:
            return await scenario(client)
        finally:
            await client.close()
    return asyncio.run(go())


def seed(monkeypatch, html=FIXTURE):
    async def fake_fetch(session, url=None):
        return html
    monkeypatch.setattr(power, "fetch_html", fake_fetch)
    asyncio.run(power.refresh(None))


def test_power_endpoint_empty_then_filled(monkeypatch, admin_headers):
    async def f(c):
        j = await (await c.get("/api/power")).json()
        assert j["snapshot"] is None and j["queues"] == power.QUEUES and j["enabled"] is True
    run_api(f)
    seed(monkeypatch)

    async def g(c):
        j = await (await c.get("/api/power")).json()
        day = j["snapshot"]["days"]["2026-11-08"]
        assert day["queues"]["1-І"] == [[0, 90], [1110, 1350]] and j["stale"] is False and j["fetched"]
        assert "last_error" not in j                                   # технічні подробиці лише для адмінів
        assert "last_error" in await (await c.get("/api/power", headers=admin_headers)).json()
    run_api(g)


def test_power_endpoint_marks_stale_data(monkeypatch):
    seed(monkeypatch)
    power.last_ok = datetime.now(K) - timedelta(hours=2)

    async def f(c):
        j = await (await c.get("/api/power")).json()
        assert j["stale"] is True and j["stale_minutes"] >= 120
    run_api(f)


def test_manual_refresh_needs_admin_and_notifies_on_change(monkeypatch, admin_headers, user_headers):
    seed(monkeypatch)
    changed = FIXTURE.replace("з 04:30 до 08:30;<br>\n3 черга, 3-ІІ", "з 05:00 до 09:00;<br>\n3 черга, 3-ІІ")
    calls = []

    async def notifier(old, new, d):
        calls.append(d)
    power.NOTIFIER = notifier

    async def fake_fetch(session, url=None):
        return changed
    monkeypatch.setattr(power, "fetch_html", fake_fetch)

    async def f(c):
        assert (await c.post("/api/admin/power/refresh")).status == 403
        assert (await c.post("/api/admin/power/refresh", headers=user_headers)).status == 403
        r = await c.post("/api/admin/power/refresh", headers=admin_headers)
        assert r.status == 200 and (await r.json())["changed"] is True and len(calls) == 1
        assert (await (await c.post("/api/admin/power/refresh", headers=admin_headers)).json())["changed"] is False
    run_api(f)


def test_manual_refresh_reports_site_failure(monkeypatch, admin_headers):
    async def broken(session, url=None):
        raise ConnectionError("сайт недоступний")
    monkeypatch.setattr(power, "fetch_html", broken)

    async def f(c):
        r = await c.post("/api/admin/power/refresh", headers=admin_headers)
        assert r.status == 502 and "недоступний" in (await r.json())["detail"]
        assert (await (await c.get("/api/power", headers=admin_headers)).json())["last_error"].startswith("ConnectionError")
    run_api(f)


# ---------- сповіщення в чат (main.py) ----------

@pytest.fixture
def main_module(monkeypatch):
    import main
    bot = FakeBot()
    monkeypatch.setattr(main, "bot", bot)
    return main, bot


def test_chat_message_on_change_respects_setting_and_night_silence(main_module, monkeypatch):
    main, bot = main_module
    base = power.parse_html(FIXTURE)
    new = power.parse_html(FIXTURE.replace("з 04:30 до 08:30;<br>\n3 черга, 3-ІІ", "з 05:00 до 09:00;<br>\n3 черга, 3-ІІ"))
    change = power.diff(base, new)
    monkeypatch.setattr(main, "_power_silent", lambda: False)
    asyncio.run(main._notify_power_change(base, new, change))
    assert len(bot.sent) == 1 and bot.sent[0][0] == main.config.CHAT_ID and "3 черга (3-І)" in bot.sent[0][1] and bot.sent[0][2] is False
    monkeypatch.setattr(main, "_power_silent", lambda: True)
    asyncio.run(main._notify_power_change(base, new, change))
    assert bot.sent[1][2] is True                                      # вночі - без звуку
    settings.set_value("power_notify", False)
    asyncio.run(main._notify_power_change(base, new, change))
    assert len(bot.sent) == 2                                          # вимкнено адміном - тиша


def test_watch_power_warns_admins_once_and_reports_recovery(main_module, monkeypatch):
    main, _ = main_module
    notified = AsyncMock()
    monkeypatch.setattr(main, "notify_admins", notified)
    state = {"n": 0, "stale": 99999}
    monkeypatch.setattr(power, "stale_seconds", lambda: state["stale"])

    async def fake_run_once(session):
        state["n"] += 1
        if state["n"] == 1:
            raise ConnectionError("down")
        state["stale"] = 5                                             # сайт ожив
        return {"diff": None, "first": False, "snapshot": {}, "previous": None}
    monkeypatch.setattr(power, "run_once", fake_run_once)
    calls = {"n": 0}

    async def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *a, **k: AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.watch_power())
    texts = [c.args[0] for c in notified.await_args_list]
    assert len(texts) == 2 and "не оновлюється" in texts[0] and "знову оновлюється" in texts[1]
