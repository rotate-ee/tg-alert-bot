"""
HTTP API для міні-застосунку (aiohttp): стан тривог, розклад, канікули і адмін-дії.
Читання відкрите; адмін-дії вимагають заголовок X-Init-Data (Telegram initData),
підпис якого перевіряється токеном бота, а user.id має бути в ADMIN_IDS.
"""
import hashlib
import hmac
import json
import logging
import os
import time as _time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from aiohttp import web

import alert_log
import bell_overrides
import cancellations
import config
import extra_lessons
import holidays
import lessons

log = logging.getLogger("api")
KYIV_TZ = ZoneInfo("Europe/Kyiv")
MAX_DAYS = 31


def verify_init_data(init_data: str) -> dict | None:
    """Повертає user із initData, якщо підпис і свіжість в порядку."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", None)
        if not got:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got):
            return None
        if _time.time() - int(pairs.get("auth_date", "0")) > 86400:
            return None
        return json.loads(pairs["user"])
    except Exception:
        return None


def _admin(request: web.Request) -> dict | None:
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    return user if user and user.get("id") in config.ADMIN_IDS else None


def _d(raw: str) -> date:
    return date.fromisoformat(raw)


def _hhmm(raw: str) -> str:
    h, m = raw.split(":")
    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        raise ValueError("time")
    return f"{int(h):02d}:{int(m):02d}"


@web.middleware
async def cors(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        except (ValueError, KeyError, TypeError):
            resp = web.json_response({"error": "bad_request"}, status=400)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _alert_block(watcher) -> dict:
    hist = alert_log.recent(30)
    open_ = hist[0] if hist and hist[0]["end"] is None and watcher.is_active else None
    today = datetime.now(KYIV_TZ).date().isoformat()
    n, mins = 0, 0
    for h in hist:
        if h["start"][:10] != today:
            continue
        n += 1
        end = datetime.fromisoformat(h["end"]) if h["end"] else datetime.now(KYIV_TZ)
        mins += max(0, int((end - datetime.fromisoformat(h["start"])).total_seconds() // 60))
    return {
        "active": watcher.is_active,
        "level": watcher.level,
        "since": open_["start"] if open_ else None,
        "last_ok": watcher.last_ok.isoformat(timespec="seconds") if watcher.last_ok else None,
        "region": config.REGION_TITLE,
        "today": {"count": n, "minutes": mins},
        "history": hist[:15],
    }


def _day_block(d: date) -> dict:
    hol = holidays.get_holiday(d)
    items = []
    if d.weekday() < 5:
        for l in lessons.get_lessons_for_date(d):
            items.append({
                "number": l["number"], "name": l["name"], "start": l["start"], "end": l["end"],
                "room": l["room"] if lessons.is_school_week(d) is not False else None,
                "cancelled": l["cancelled"], "remote": lessons.is_lesson_remote_today(l, d),
            })
    return {"school": lessons.is_school_week(d), "holiday": hol, "lessons": items}


async def state(request: web.Request) -> web.Response:
    watcher = request.app["watcher"]
    start = _d(request.query["from"]) if "from" in request.query else datetime.now(KYIV_TZ).date()
    n = max(1, min(int(request.query.get("days", "7")), MAX_DAYS))
    return web.json_response({
        "now": datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
        "alert": _alert_block(watcher),
        "days": {(start + timedelta(days=i)).isoformat(): _day_block(start + timedelta(days=i)) for i in range(n)},
        "holidays": holidays.list_all(),
    })


async def me(request: web.Request) -> web.Response:
    return web.json_response({"admin": _admin(request) is not None})


def _need_admin(request: web.Request) -> dict:
    user = _admin(request)
    if user is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    return user


async def a_cancel(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d, num = _d(b["date"]), int(b["number"])
    if b.get("cancel", True):
        cancellations.cancel_lesson(d, num)
    else:
        cancellations.uncancel_lesson(d, num)
    return web.json_response({"ok": True})


async def a_time(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d, num = _d(b["date"]), int(b["number"])
    if b.get("clear"):
        bell_overrides.clear_override(d, num)
    else:
        bell_overrides.set_override(d, num, _hhmm(b["start"]), _hhmm(b["end"]))
    return web.json_response({"ok": True})


async def a_lesson(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d, num = _d(b["date"]), int(b["number"])
    if b.get("remove"):
        extra_lessons.remove_extra_lesson(d, num)
    else:
        name = str(b["name"]).strip()[:60]
        if not name:
            raise ValueError("name")
        extra_lessons.add_extra_lesson(d, num, name, _hhmm(b["start"]), _hhmm(b["end"]))
    return web.json_response({"ok": True})


async def a_holiday(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    if b.get("remove"):
        holidays.remove(str(b["remove"]))
    else:
        d1, d2 = _d(b["from"]), _d(b["to"])
        if d2 < d1:
            raise ValueError("range")
        holidays.add(d1, d2, str(b.get("title", ""))[:60])
    return web.json_response({"ok": True, "holidays": holidays.list_all()})


async def a_announce(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    text = str(b["text"]).strip()[:3500]
    if not text:
        raise ValueError("text")
    await request.app["bot"].send_message(config.CHAT_ID, text)
    return web.json_response({"ok": True})


async def start(bot, watcher) -> web.AppRunner:
    app = web.Application(middlewares=[cors])
    app["bot"], app["watcher"] = bot, watcher
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/me", me)
    app.router.add_post("/api/admin/cancel", a_cancel)
    app.router.add_post("/api/admin/time", a_time)
    app.router.add_post("/api/admin/lesson", a_lesson)
    app.router.add_post("/api/admin/holiday", a_holiday)
    app.router.add_post("/api/admin/announce", a_announce)
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("API запущено на порту %s", port)
    return runner
