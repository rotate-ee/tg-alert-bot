import asyncio
import io
import json
import zipfile

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

import backup
import notify


class FakeBot:
    def __init__(self, blocked=()):
        self.sent, self.blocked = [], set(blocked)

    async def send_message(self, uid, text, parse_mode=None):
        if uid in self.blocked:
            raise TelegramForbiddenError(method=SendMessage(chat_id=uid, text=text), message="Forbidden: bot was blocked by the user")
        self.sent.append((uid, text))


def test_topics_roundtrip_and_unknown_topics_ignored():
    assert notify.set_topics(7, ["alerts", "nonsense"], "Оля") == ["alerts"]
    assert notify.get_topics(7) == ["alerts"] and notify.subscribers("alerts") == [7]
    assert notify.set_topics(7, []) == [] and notify.subscribers("alerts") == []


def test_send_removes_users_who_blocked_the_bot():
    notify.set_topics(1, ["alerts"])
    notify.set_topics(2, ["alerts", "changes"])
    bot = FakeBot(blocked={2})
    assert asyncio.run(notify.send_to_subscribers(bot, "alerts", "тест")) == 1
    assert [u for u, _ in bot.sent] == [1]
    assert notify.get_topics(2) == []  # заблокував бота -> автоматично відписаний
    assert notify.get_topics(1) == ["alerts"]


def test_changes_are_batched_and_deduplicated():
    notify.record_change("2026-11-05", "3-й урок: скасовано")
    notify.record_change("2026-11-05", "3-й урок: скасовано")
    notify.record_change("2026-11-05", "4-й урок: нова година 14:00–14:45")
    assert notify.due_changes(quiet_seconds=60) == []  # ще не минула пауза
    (date_iso, lines, _), = notify.due_changes(quiet_seconds=0)
    assert date_iso == "2026-11-05" and len(lines) == 2
    assert notify.due_changes(quiet_seconds=0) == []  # вже відправлені


def seed_files():
    (backup.DATA_DIR / "holidays.json").write_text(json.dumps([{"id": "a", "title": "Канікули"}]), encoding="utf-8")
    (backup.DATA_DIR / "settings.json").write_text(json.dumps({"alerts_muted": True}), encoding="utf-8")


def test_backup_roundtrip():
    seed_files()
    payload, name, names = backup.make_zip()
    assert name.startswith("backup_") and sorted(names) == ["holidays.json", "settings.json"]
    (backup.DATA_DIR / "holidays.json").write_text("[]", encoding="utf-8")
    assert backup.restore_zip(payload) == ["holidays.json", "settings.json"]
    assert json.loads((backup.DATA_DIR / "holidays.json").read_text(encoding="utf-8"))[0]["title"] == "Канікули"


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, content in files.items():
            z.writestr(n, content)
    return buf.getvalue()


@pytest.mark.parametrize("files,code", [
    ({"../evil.json": "{}"}, "bad_name"),
    ({"sub/dir.json": "{}"}, "bad_name"),
    ({"script.py": "print(1)"}, "bad_name"),
    ({"ok.json": "{not json"}, "bad_json"),
    ({}, "empty"),
])
def test_restore_rejects_bad_archives_and_changes_nothing(files, code):
    seed_files()
    before = (backup.DATA_DIR / "settings.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=code):
        backup.restore_zip(make_zip(files))
    assert (backup.DATA_DIR / "settings.json").read_text(encoding="utf-8") == before


def test_restore_rejects_non_zip_and_all_or_nothing():
    with pytest.raises(ValueError, match="not_zip"):
        backup.restore_zip(b"just text")
    seed_files()
    mixed = make_zip({"settings.json": '{"alerts_muted": false}', "bad.json": "{oops"})
    with pytest.raises(ValueError):
        backup.restore_zip(mixed)
    assert json.loads((backup.DATA_DIR / "settings.json").read_text(encoding="utf-8"))["alerts_muted"] is True
