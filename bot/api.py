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
import duties
import duty_overrides
import early_end
import extra_lessons
import holidays
import lessons
import remote_overrides
import remote_periods
import renumber
import settings

log = logging.getLogger("api")
KYIV_TZ = ZoneInfo("Europe/Kyiv")
MAX_DAYS = 31
STARTED_AT = _time.time()


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
        extra_nums = {e["number"] for e in extra_lessons.get_extra_lessons(d)}
        school = lessons.is_school_week(d)
        for l in lessons.get_lessons_for_date(d):
            items.append({
                "number": l["number"], "name": l["name"], "start": l["start"], "end": l["end"],
                "room": l["room"] if school is not False else None,
                "cancelled": l["cancelled"], "remote": lessons.is_lesson_remote_today(l, d),
                "remote_capable": bool(l["remote"]) and school is not False,
                "early_end": early_end.get_early_end(d, l["number"]),
                "extra": l["number"] in extra_nums,
            })
    return {"school": lessons.is_school_week(d), "holiday": hol, "lessons": items, "remote_period": remote_periods.get_period(d), "renumbered": bool(renumber.get_map(d))}


async def state(request: web.Request) -> web.Response:
    cfg = settings.load()
    if cfg["maintenance"] and _admin(request) is None:
        # технічні роботи: звичайним користувачам дані не віддаємо
        return web.json_response({
            "now": datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
            "maintenance": {"message": cfg["maintenance_message"]},
        })
    watcher = request.app["watcher"]
    start = _d(request.query["from"]) if "from" in request.query else datetime.now(KYIV_TZ).date()
    n = max(1, min(int(request.query.get("days", "7")), MAX_DAYS))
    return web.json_response({
        "now": datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
        "alert": _alert_block(watcher),
        "days": {(start + timedelta(days=i)).isoformat(): _day_block(start + timedelta(days=i)) for i in range(n)},
        "holidays": holidays.list_all(),
        "remote_periods": remote_periods.list_all(),
        "settings": settings.load(),
    })


async def me(request: web.Request) -> web.Response:
    return web.json_response({"admin": _admin(request) is not None})


async def duty_schedule(request: web.Request) -> web.Response:
    # Імена класу не віддаємо публічному API: потрібен підписаний запуск у Telegram.
    if verify_init_data(request.headers.get("X-Init-Data", "")) is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    return web.json_response({
        "configured": bool(config.DUTY_STUDENTS),
        "start_date": config.DUTY_START_DATE,
        "students_list": list(config.DUTY_STUDENTS),
        "days": duties.upcoming(),
    })


def _need_admin(request: web.Request) -> dict:
    user = _admin(request)
    if user is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    return user


async def a_duty(request: web.Request) -> web.Response:
    _need_admin(request)
    body = await request.json()
    day = _d(body["date"])
    if body.get("reset"):
        duty_overrides.clear_override(day)
        return web.json_response({"ok": True})

    pair = body.get("students")
    if (not isinstance(pair, list) or len(pair) != 2
            or any(not isinstance(name, str) or name not in config.DUTY_STUDENTS for name in pair)
            or pair[0] == pair[1]):
        raise ValueError("students")
    duty_overrides.set_override(day, pair)
    return web.json_response({"ok": True})


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
        start, end = _hhmm(b["start"]), _hhmm(b["end"])
        bell_overrides.set_override(d, num, start, end)
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
        start, end = _hhmm(b["start"]), _hhmm(b["end"])
        extra_lessons.add_extra_lesson(d, num, name, start, end)
    return web.json_response({"ok": True})


async def a_endnow(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d, num = _d(b["date"]), int(b["number"])
    if b.get("clear"):
        early_end.clear_early_end(d, num)
    else:
        end = _hhmm(b["time"]) if b.get("time") else datetime.now(KYIV_TZ).strftime("%H:%M")
        early_end.mark_early_end(d, num, end)
    return web.json_response({"ok": True})


async def a_inperson(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d, num = _d(b["date"]), int(b["number"])
    if b.get("value", True):
        remote_overrides.mark_in_person(d, num)
    else:
        remote_overrides.clear_in_person(d, num)
    return web.json_response({"ok": True})


async def a_settings(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    settings.set_value(str(b["key"]), b["value"])
    return web.json_response({"ok": True, "settings": settings.load()})


async def a_overview(request: web.Request) -> web.Response:
    """Усі розпорядження адміна на найближчі 3 тижні + діагностика системи."""
    _need_admin(request)
    today = datetime.now(KYIV_TZ).date()
    changes = []
    for i in range(21):
        d = today + timedelta(days=i)
        if d.weekday() > 4 or holidays.get_holiday(d):
            continue
        if renumber.get_map(d):
            changes.append({"date": d.isoformat(), "number": None, "name": "Нумерація уроків", "kind": "renumber", "text": "змінено порядок номерів"})
        for l in lessons.get_lessons_for_date(d):
            n = l["number"]
            base = {"date": d.isoformat(), "number": n, "name": l["name"]}
            if l["cancelled"]:
                changes.append({**base, "kind": "cancel", "text": "скасовано"})
            if bell_overrides.get_override(d, n):
                changes.append({**base, "kind": "time", "text": f"час {l['start']}-{l['end']}"})
            if early_end.get_early_end(d, n):
                changes.append({**base, "kind": "early", "text": f"завершено о {early_end.get_early_end(d, n)}"})
            if remote_overrides.is_in_person(d, n):
                changes.append({**base, "kind": "inperson", "text": "очно (підтверджено)"})
        for e in extra_lessons.get_extra_lessons(d):
            changes.append({"date": d.isoformat(), "number": e["number"], "name": e["name"], "kind": "extra", "text": f"додатковий {e['start']}-{e['end']}"})
    files = {}
    ddir = cancellations.DATA_DIR
    for p in sorted(ddir.glob("*.json")):
        files[p.name] = p.stat().st_size
    return web.json_response({
        "changes": changes,
        "diag": {
            "data_dir": str(ddir),
            "persistent": bool(os.getenv("DATA_DIR")),
            "files": files,
            "uptime_min": int((_time.time() - STARTED_AT) // 60),
            "admins": len(config.ADMIN_IDS),
            "region": config.REGION_TITLE,
        },
    })


def _swap_flags(d: date, a: int, b: int) -> None:
    """Після зміни номерів прапорці (скасовано / очно) йдуть за уроком, а час і дострокове завершення скидаються."""
    ca = cancellations.get_cancelled_numbers(d)
    a_c, b_c = a in ca, b in ca
    a_p, b_p = remote_overrides.is_in_person(d, a), remote_overrides.is_in_person(d, b)
    for n, c, p in ((a, b_c, b_p), (b, a_c, a_p)):
        if c:
            cancellations.cancel_lesson(d, n)
        else:
            cancellations.uncancel_lesson(d, n)
        if p:
            remote_overrides.mark_in_person(d, n)
        else:
            remote_overrides.clear_in_person(d, n)
        bell_overrides.clear_override(d, n)
        early_end.clear_early_end(d, n)


async def a_renumber(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d = _d(b["date"])
    if b.get("reset"):
        renumber.clear(d)
        return web.json_response({"ok": True})
    cur, tgt = int(b["number"]), int(b["target"])
    if tgt != 0 and tgt not in lessons.load_schedule()["bells"]:
        raise ValueError("target")
    if cur == tgt:
        return web.json_response({"ok": True})
    current = {l["number"] for l in lessons.get_lessons_for_date(d)}
    extra = {e["number"] for e in extra_lessons.get_extra_lessons(d)}
    if cur not in current or cur in extra or tgt in extra:
        raise ValueError("number")
    rmap = renumber.get_map(d)
    inv = {v: k for k, v in rmap.items()}
    rmap[inv.get(cur, cur)] = tgt
    if tgt in current:  # слот зайнятий - уроки міняються місцями
        rmap[inv.get(tgt, tgt)] = cur
    renumber.set_map(d, {k: v for k, v in rmap.items() if k != v})
    _swap_flags(d, cur, tgt)
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


async def a_remote_period(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    if b.get("remove"):
        remote_periods.remove(str(b["remove"]))
    else:
        d1, d2 = _d(b["from"]), _d(b["to"])
        if d2 < d1:
            raise ValueError("range")
        remote_periods.add(d1, d2, str(b.get("title", ""))[:60])
    return web.json_response({"ok": True, "remote_periods": remote_periods.list_all()})


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
    app.router.add_get("/api/duties", duty_schedule)
    app.router.add_post("/api/admin/duty", a_duty)
    app.router.add_post("/api/admin/cancel", a_cancel)
    app.router.add_post("/api/admin/time", a_time)
    app.router.add_post("/api/admin/lesson", a_lesson)
    app.router.add_post("/api/admin/holiday", a_holiday)
    app.router.add_post("/api/admin/remote_period", a_remote_period)
    app.router.add_post("/api/admin/endnow", a_endnow)
    app.router.add_post("/api/admin/renumber", a_renumber)
    app.router.add_post("/api/admin/inperson", a_inperson)
    app.router.add_post("/api/admin/settings", a_settings)
    app.router.add_get("/api/admin/overview", a_overview)
    app.router.add_post("/api/admin/announce", a_announce)
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("API запущено на порту %s", port)
    return runner
