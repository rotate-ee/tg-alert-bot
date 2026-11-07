"""Спільне оточення тестів: тимчасовий DATA_DIR, фейкові токени, підпис Telegram initData."""
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import time
from urllib.parse import urlencode

import pytest

DATA = tempfile.mkdtemp(prefix="botdata_")
os.environ.update(
    BOT_TOKEN="123:test-token", CHAT_ID="1", ALERTS_TOKEN="x", ADMIN_IDS="42",
    DATA_DIR=DATA, SCHOOL_WEEK_ANCHOR_MONDAY="2026-09-07", DUTY_STUDENTS="[]",
)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bot"))


def init_data(user_id: int) -> str:
    """Дійсний (підписаний токеном бота) рядок Telegram.WebApp.initData для тестів."""
    import config
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "Тест"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture(autouse=True)
def clean_data():
    for p in pathlib.Path(DATA).glob("*"):
        if p.is_file():
            p.unlink()
    import alert_stats
    import notify
    alert_stats._cache.clear()
    notify._pending.clear()
    yield


@pytest.fixture
def admin_headers():
    return {"X-Init-Data": init_data(42)}


@pytest.fixture
def user_headers():
    return {"X-Init-Data": init_data(7)}
