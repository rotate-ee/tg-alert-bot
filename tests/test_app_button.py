"""Кнопка "Відкрити застосунок" замість згадок команди /app."""
import asyncio
from types import SimpleNamespace

import pytest

import users


class FakeBot:
    def __init__(self, main_app=True):
        self.main_app = main_app

    async def get_me(self):
        return SimpleNamespace(username="test_bot", has_main_web_app=self.main_app)


class Msg:
    def __init__(self, text, chat_type="private", user_id=7):
        self.text, self.answers = text, []
        self.chat = SimpleNamespace(type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, model_dump=lambda: {"id": user_id, "first_name": "Тест"})

    async def answer(self, text, **kw):
        self.answers.append((text, kw))


@pytest.fixture
def main_module(monkeypatch):
    import main
    main._bot_info.clear()
    monkeypatch.setattr(main, "bot", FakeBot())
    monkeypatch.setattr(main.config, "MINIAPP_LINK", "")
    return main


def button(kb):
    return kb.inline_keyboard[0][0]


def test_private_chat_gets_a_real_web_app_button(main_module):
    b = button(asyncio.run(main_module.app_keyboard(True)))
    assert b.text == "📅 Відкрити застосунок" and b.web_app.url.startswith("https://app.example/index.html") and b.url is None


def test_group_chat_gets_a_link_button(main_module, monkeypatch):
    assert button(asyncio.run(main_module.app_keyboard(False))).url == "https://t.me/test_bot?startapp"      # головний застосунок налаштовано
    main_module._bot_info.clear()
    monkeypatch.setattr(main_module, "bot", FakeBot(main_app=False))
    assert button(asyncio.run(main_module.app_keyboard(False))).url == "https://t.me/test_bot?start=app"     # запасний варіант
    monkeypatch.setattr(main_module.config, "MINIAPP_LINK", "https://t.me/test_bot/app")
    assert button(asyncio.run(main_module.app_keyboard(False))).url == "https://t.me/test_bot/app"           # явне посилання з налаштувань


def test_no_button_when_the_app_is_not_configured(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "MINIAPP_URL", "")
    assert asyncio.run(main_module.app_keyboard(True)) is None and asyncio.run(main_module.app_keyboard(False)) is None


def test_help_has_a_button_and_no_app_command_text(main_module):
    for chat_type, attr in (("private", "web_app"), ("supergroup", "url")):
        msg = Msg("/help", chat_type)
        asyncio.run(main_module.cmd_help(msg))
        text, kw = msg.answers[0]
        assert "/app" not in text and "/now" in text
        assert getattr(button(kw["reply_markup"]), attr) is not None


def test_start_with_app_payload_opens_the_app_directly_and_counts_the_start(main_module):
    msg = Msg("/start app")
    asyncio.run(main_module.cmd_help(msg))
    assert len(msg.answers) == 1 and button(msg.answers[0][1]["reply_markup"]).web_app is not None
    assert users.stats()["totals"]["started"] == 1


def test_app_command_in_a_group_gives_a_link_button_instead_of_a_hint(main_module):
    msg = Msg("/app", "group")
    asyncio.run(main_module.cmd_app(msg))
    text, kw = msg.answers[0]
    assert "особистих повідомленнях" not in text and button(kw["reply_markup"]).url.startswith("https://t.me/test_bot")


def test_overview_reports_whether_main_app_is_configured(admin_headers):
    import api
    from aiohttp.test_utils import TestClient, TestServer
    from alerts import AlertWatcher

    async def go(main_app):
        client = TestClient(TestServer(api.build_app(FakeBot(main_app), AlertWatcher("x", "M"))))
        await client.start_server()
        try:
            return (await (await client.get("/api/admin/overview", headers=admin_headers)).json())["diag"]["main_app"]
        finally:
            await client.close()
    assert asyncio.run(go(True)) is True and asyncio.run(go(False)) is False
