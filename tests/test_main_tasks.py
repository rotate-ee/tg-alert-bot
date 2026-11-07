"""Фонові задачі бота: особисті сповіщення, розсилка змін, попередження про застарілі дані."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import notify

K = ZoneInfo("Europe/Kyiv")


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, parse_mode=None):
        self.sent.append((uid, text))


@pytest.fixture
def main_module(monkeypatch):
    import main
    bot = FakeBot()
    monkeypatch.setattr(main, "bot", bot)
    return main, bot


def test_personal_alert_goes_only_to_alert_subscribers(main_module):
    main, bot = main_module
    notify.set_topics(1, ["alerts"])
    notify.set_topics(2, ["changes"])
    asyncio.run(main._personal_alert("started", "red"))
    assert [u for u, _ in bot.sent] == [1] and "Тривога" in bot.sent[0][1] and "Червоний" in bot.sent[0][1]
    bot.sent.clear()
    asyncio.run(main._personal_alert("ended", None))
    assert [u for u, _ in bot.sent] == [1] and "Відбій" in bot.sent[0][1]
    bot.sent.clear()
    asyncio.run(main._personal_alert("level_changed", "yellow"))
    assert "Рівень змінився" in bot.sent[0][1] and "Жовтий" in bot.sent[0][1]


def test_changes_text_is_escaped_and_dated(main_module):
    main, _ = main_module
    text = main._changes_text("2026-11-05", ["3-й урок (Хімія): скасовано", "<b>HTML</b>"])
    assert "05.11" in text and "(чт)" in text and "&lt;b&gt;HTML" in text and "• 3-й урок" in text


def test_changes_dispatcher_sends_batched_message(main_module, monkeypatch):
    main, bot = main_module
    notify.set_topics(5, ["changes"])
    notify.record_change("2026-11-05", "3-й урок: скасовано")
    notify.record_change("2026-11-05", "4-й урок: очно")
    monkeypatch.setattr(notify, "due_changes", lambda quiet_seconds=25.0, _orig=notify.due_changes: _orig(0))
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.changes_dispatcher())
    assert len(bot.sent) == 1 and bot.sent[0][0] == 5 and bot.sent[0][1].count("•") == 2


def test_watch_staleness_warns_once_and_reports_recovery(main_module, monkeypatch):
    main, _ = main_module
    notified = AsyncMock()
    monkeypatch.setattr(main, "notify_admins", notified)
    watcher = main.alert_watcher
    watcher.last_ok = datetime.now(K) - timedelta(minutes=10)
    watcher.last_error = "TimeoutError: boom"
    calls = {"n": 0}

    async def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] == 3:
            watcher.last_ok = datetime.now(K)      # дані ожили
        if calls["n"] >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.watch_staleness())
    texts = [c.args[0] for c in notified.await_args_list]
    assert len(texts) == 2                                  # попередження лише раз + повідомлення про відновлення
    assert "не оновлюються" in texts[0] and "TimeoutError" in texts[0] and "знову оновлюються" in texts[1]
