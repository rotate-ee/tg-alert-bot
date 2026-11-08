"""Створює JSON з реальними відповідями API (для перевірки міні-застосунку в Node):
    python tests/make_fixture.py /tmp/fixture.json [--rich]"""
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


def seed_rich(now: datetime) -> None:
    """Реалістична історія за ~150 днів (детермінована) - для перевірки статистики та скріншотів."""
    import json as _json
    import random
    rnd = random.Random(7)
    entries = []
    for back in range(150, 0, -1):
        day = (now - timedelta(days=back)).date()
        for _ in range(rnd.choice([0, 0, 1, 1, 2, 3, 5])):
            hour = rnd.choices(range(24), weights=[5, 6, 7, 6, 8, 6, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 5])[0]
            start = datetime(day.year, day.month, day.day, hour, rnd.randrange(60), tzinfo=K)
            end = start + timedelta(minutes=rnd.choice([8, 12, 20, 25, 35, 50, 90, 140]))
            entries.append({"id": f"r{len(entries):04d}", "start": start.isoformat(timespec="seconds"),
                            "end": end.isoformat(timespec="seconds"), "level": "yellow" if rnd.random() < 0.2 else "red",
                            "is_false": rnd.random() < 0.03})
    alert_log.DATA_PATH.write_text(_json.dumps(entries), encoding="utf-8")
    alert_log.on_started("red")                    # поточна тривога


def seed_users(now: datetime) -> None:
    """Реєстр користувачів із запусками бота за ~45 днів (детермінований)."""
    import json as _json
    import random
    import users
    rnd = random.Random(11)
    data = {"users": {}, "events": []}
    for uid in range(1, 38):
        first = now - timedelta(days=rnd.randrange(1, 45), hours=rnd.randrange(24))
        starts = rnd.choice([1, 1, 1, 2, 3])
        data["users"][str(uid)] = {"first": first.isoformat(timespec="seconds"), "last": first.isoformat(timespec="seconds"),
                                   "starts": starts, "blocked": rnd.random() < 0.1, "name": rnd.choice(["Оля", "Іван", "Марія", "Андрій", "Софія", "Максим", "Дарина"]) + f" {uid}",
                                   "username": f"user{uid}" if rnd.random() < 0.6 else ""}
        for k in range(starts):
            t = first + timedelta(days=k * rnd.randrange(1, 9))
            if t <= now:
                data["events"].append({"t": t.isoformat(timespec="seconds"), "uid": uid, "new": k == 0})
    data["events"].sort(key=lambda e: e["t"])
    users.DATA_PATH.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")


async def main(out: str, rich: bool = False) -> None:
    now = datetime.now(K)
    seed_rich(now) if rich else seed(now)
    seed_users(now)
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
        "users_stats": await (await get("/api/admin/users")).json(),
        "init_data": h["X-Init-Data"],
    }
    await client.close()
    pathlib.Path(out).write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    print("fixture written:", out, "| days:", len(fixture["state"]["days"]), "| stale:", fixture["state"]["alert"]["stale"])


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(args[0] if args else "fixture.json", rich="--rich" in sys.argv))
