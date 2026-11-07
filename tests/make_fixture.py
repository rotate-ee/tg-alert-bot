"""Створює JSON з реальними відповідями API (для перевірки міні-застосунку в Node):
    python tests/make_fixture.py /tmp/fixture.json"""
import asyncio
import json
import pathlib
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import conftest  # noqa: E402,F401  (виставляє змінні оточення і шлях до bot/)
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import alert_log  # noqa: E402
import api  # noqa: E402
import holidays  # noqa: E402
import lessons  # noqa: E402
from alerts import AlertWatcher  # noqa: E402

K = ZoneInfo("Europe/Kyiv")


class Bot:
    async def get_me(self):
        class Me:
            username = "test_bot"
        return Me()


def seed(now: datetime) -> None:
    day = now.date() - timedelta(days=1)          # останній навчальний день із уроками -> тривога під час уроку
    for _ in range(30):
        if day.weekday() < 5 and not holidays.get_holiday(day) and not lessons.schedule_ended(day):
            first = next((l for l in lessons.get_lessons_for_date(day) if not l["cancelled"]), None)
            if first:
                h, m = map(int, first["start"].split(":"))
                start = datetime(day.year, day.month, day.day, h, m, tzinfo=K) + timedelta(minutes=5)
                alert_log.add(start.strftime("%Y-%m-%d %H:%M"), (start + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M"), "red")
                break
        day -= timedelta(days=1)
    night = datetime.combine(now.date() - timedelta(days=2), time(23, 10), tzinfo=K)
    alert_log.add(night.strftime("%Y-%m-%d %H:%M"), (night + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"), "yellow")
    alert_log.add((night - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), (night - timedelta(days=1) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"), "red", is_false=True)
    alert_log.on_started("red")                    # поточна тривога


async def main(out: str) -> None:
    now = datetime.now(K)
    seed(now)
    watcher = AlertWatcher("x", "Миколаївська область")
    watcher._was_active, watcher._level = True, "red"
    watcher.last_ok = now - timedelta(minutes=10)  # дані застарілі
    watcher.last_error = "TimeoutError: тестова помилка"
    client = TestClient(TestServer(api.build_app(Bot(), watcher)))
    await client.start_server()
    h = {"X-Init-Data": conftest.init_data(42)}
    start = (now - timedelta(days=8)).date().isoformat()
    get = lambda p: client.get(p, headers=h)
    fixture = {
        "state": await (await get(f"/api/state?from={start}&days=21")).json(),
        "me": await (await get("/api/me")).json(),
        "alert_stats": await (await get("/api/alert_stats")).json(),
        "overview": await (await get("/api/admin/overview")).json(),
        "activity": await (await get("/api/admin/activity")).json(),
        "duties": await (await get("/api/duties")).json(),
        "init_data": h["X-Init-Data"],
    }
    await client.close()
    pathlib.Path(out).write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    print("fixture written:", out, "| days:", len(fixture["state"]["days"]), "| stale:", fixture["state"]["alert"]["stale"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fixture.json"))
