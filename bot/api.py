"""
HTTP API для міні-застосунку (aiohttp): стан тривог, розклад, канікули і адмін-дії.
Читання відкрите; адмін-дії вимагають заголовок X-Init-Data (Telegram initData),
підпис якого перевіряється токеном бота, а user.id має бути в ADMIN_IDS.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time as _time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from aiohttp import web

import alert_log
import alert_stats
import activity
import backup
import bell_overrides
import cancellations
import config
import duties
import duty_overrides
import early_end
import extra_lessons
import holidays
import lessons
import notify
import remote_overrides
import remote_lesson_overrides
import remote_periods
import renumber
import settings
import week_shifts

log = logging.getLogger("api")
KYIV_TZ = ZoneInfo("Europe/Kyiv")
MAX_DAYS = 31
STARTED_AT = _time.time()
_report_last_sent: dict[int, float] = {}


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


CHANGE_PATHS = {
    "/api/admin/cancel", "/api/admin/time", "/api/admin/lesson", "/api/admin/endnow",
    "/api/admin/inperson", "/api/admin/remote", "/api/admin/renumber",
    "/api/admin/holiday", "/api/admin/remote_period", "/api/admin/week_shift",
}
SUBGROUP_LABEL = {"girls": " (дівчата)", "boys": " (хлопці)"}


def _fd(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}"


def _lesson_label(d: date, number: int) -> str:
    try:
        name = next((l["name"] for l in lessons.get_lessons_for_date(d) if l["number"] == number), "")
    except Exception:
        name = ""
    return f"{number}-й урок" + (f" ({name})" if name else "")


def _describe_change(path: str, b: dict) -> tuple[str, str] | None:
    """Короткий опис зміни розкладу для особистих сповіщень: (дата-ключ, рядок) або None."""
    today = datetime.now(KYIV_TZ).date()
    if path in ("/api/admin/holiday", "/api/admin/remote_period", "/api/admin/week_shift"):
        if b.get("remove"):
            what = {"/api/admin/holiday": "канікули", "/api/admin/remote_period": "дистанційний період",
                    "/api/admin/week_shift": "чергування тижнів"}[path]
            return today.isoformat(), f"Змінено {what}: дивіться застосунок"
        if path == "/api/admin/holiday":
            return b["from"], f"Канікули «{str(b.get('title', ''))[:40]}»: {_fd(b['from'])} – {_fd(b['to'])}"
        if path == "/api/admin/remote_period":
            return b["from"], f"Дистанційне навчання: {_fd(b['from'])} – {_fd(b['to'])}"
        return b["from"], f"З {_fd(b['from'])} тиждень {'очний' if b.get('school') else 'дистанційний'}, далі чергуються"
    d = _d(b["date"])
    if not (today <= d <= today + timedelta(days=14)):
        return None
    n = b.get("number")
    lab = _lesson_label(d, int(n)) if n is not None else ""
    lab += SUBGROUP_LABEL.get(b.get("subgroup"), "")
    if path == "/api/admin/cancel":
        text = f"{lab}: скасовано" if b.get("cancel", True) else f"{lab}: знову проводиться"
    elif path == "/api/admin/time":
        text = f"{lab}: час за розкладом" if b.get("clear") else f"{lab}: нова година {b['start']}–{b['end']}"
    elif path == "/api/admin/lesson":
        text = f"додатковий урок {n} прибрано" if b.get("remove") else f"додано урок «{str(b.get('name', ''))[:40]}» {b['start']}–{b['end']}"
    elif path == "/api/admin/endnow":
        text = f"{lab}: дострокове завершення скасовано" if b.get("clear") else f"{lab}: закінчиться раніше" + (f" (о {b['time']})" if b.get("time") else "")
    elif path == "/api/admin/inperson":
        text = f"{lab}: очно" if b.get("value", True) else f"{lab}: дистанційно"
    elif path == "/api/admin/remote":
        text = f"{lab}: формат за розкладом" if b.get("clear") else (f"{lab}: дистанційно" if b.get("value", True) else f"{lab}: очно")
    else:  # renumber
        text = "нумерацію уроків скинуто" if b.get("reset") else "змінено номери уроків"
    return d.isoformat(), text


async def _record_change(request: web.Request) -> None:
    try:
        described = _describe_change(request.path, await request.json())
        if described:
            admin = _admin(request)
            notify.record_change(described[0], described[1], admin["id"] if admin else None)
    except Exception:
        log.exception("Не вдалося записати зміну для сповіщень")


@web.middleware
async def cors(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
            if request.method == "POST" and request.path in CHANGE_PATHS and resp.status < 400:
                await _record_change(request)
        except web.HTTPException as e:
            resp = e
        except (ValueError, KeyError, TypeError):
            resp = web.json_response({"error": "bad_request"}, status=400)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _alert_block(watcher, admin: bool = False) -> dict:
    hist = alert_log.recent(200)
    open_ = hist[0] if hist and hist[0]["end"] is None and watcher.is_active else None
    today = datetime.now(KYIV_TZ).date().isoformat()
    n, mins, false_n = 0, 0, 0
    for h in hist:
        if h["start"][:10] != today:
            continue
        if h.get("is_false"):
            false_n += 1  # хибні тривоги не рахуємо в статистиці
            continue
        n += 1
        end = datetime.fromisoformat(h["end"]) if h["end"] else datetime.now(KYIV_TZ)
        mins += max(0, int((end - datetime.fromisoformat(h["start"])).total_seconds() // 60))
    seconds = watcher.stale_seconds() if hasattr(watcher, "stale_seconds") else 0
    stale = seconds > config.ALERT_STALE_SECONDS

    def with_hits(entry: dict) -> dict:
        try:
            return {**entry, "hit": alert_stats.lessons_hit(entry)}
        except Exception:
            log.exception("Не вдалося порахувати уроки під тривогою")
            return entry

    return {
        "active": watcher.is_active,
        "level": watcher.level,
        "stale": stale,
        "stale_minutes": seconds // 60 if stale else 0,
        **({"last_error": getattr(watcher, "last_error", None)} if admin else {}),
        "since": open_["start"] if open_ else None,
        "last_ok": watcher.last_ok.isoformat(timespec="seconds") if watcher.last_ok else None,
        "region": config.REGION_TITLE,
        "today": {"count": n, "minutes": mins, "false": false_n},
        "history": [with_hits(h) for h in hist[: 60 if admin else 15]],
        **({"months": alert_log.months()} if admin else {}),
    }


def _map_key(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _alert_map_block(watcher) -> dict:
    oblasts, raions, cities = {}, {}, {}

    def merge(target: dict, key: str, title: str, level: str, scope: str) -> None:
        if not key:
            return
        current = target.get(key)
        scope_priority = {"hromada": 0, "raion": 1, "oblast": 2, "city": 2}
        if (
            current is None
            or (level == "red" and current["level"] != "red")
            or (level == current["level"] and scope_priority[scope] > scope_priority[current["scope"]])
        ):
            target[key] = {"title": title, "level": level, "scope": scope}

    for alert in watcher.active_alerts:
        if alert.get("alert_type") != "air_raid":
            continue
        location_type = alert.get("location_type")
        title = (alert.get("location_title") or "").strip()
        level = "yellow" if alert.get("alert_level") == "yellow" else "red"
        if location_type == "oblast":
            merge(oblasts, _map_key(title), title, level, "oblast")
        elif location_type == "raion":
            raion = title or (alert.get("location_raion") or "").strip()
            merge(raions, _map_key(raion), raion, level, "raion")
        elif location_type == "hromada":
            raion = (alert.get("location_raion") or "").strip()
            if raion:
                merge(raions, _map_key(raion), raion, level, "hromada")
            else:
                merge(cities, _map_key(title), title, level, "hromada")
        elif location_type == "city":
            merge(cities, _map_key(title), title, level, "city")

    return {
        "oblasts": oblasts,
        "raions": raions,
        "cities": cities,
        "last_ok": watcher.last_ok.isoformat(timespec="seconds") if watcher.last_ok else None,
    }


def _day_block(d: date) -> dict:
    hol = holidays.get_holiday(d)
    ended = lessons.schedule_ended(d)
    items = []
    if d.weekday() < 5 and not ended:
        extra_nums = {e["number"] for e in extra_lessons.get_extra_lessons(d)}
        school = lessons.is_school_week(d)
        for l in lessons.get_lessons_for_date(d):
            is_remote = lessons.is_lesson_remote_today(l, d)
            items.append({
                "number": l["number"], "name": l["name"], "start": l["start"], "end": l["end"],
                "subgroup": l.get("subgroup"), "subgroup_label": l.get("subgroup_label"),
                "in_person": bool(l.get("subgroup") and remote_lesson_overrides.is_group_in_person(d, l["number"], l["subgroup"])),
                "room": l["room"] if school is not False and not is_remote else None,
                "cancelled": l["cancelled"], "remote": is_remote,
                "remote_capable": school is not False,
                "early_end": early_end.get_early_end(d, l["number"]),
                "extra": l["number"] in extra_nums,
            })
    return {"school": lessons.is_school_week(d), "holiday": hol, "lessons": items, "ended": ended, "remote_period": remote_periods.get_period(d), "renumbered": bool(renumber.get_map(d))}


def _lesson_names() -> list[str]:
    names = set()
    for entries in lessons.load_schedule()["days"].values():
        for entry in entries:
            values = entry.values() if isinstance(entry, dict) else (entry,)
            for value in values:
                if isinstance(value, str) and value:
                    names.add(value)
                elif isinstance(value, dict):
                    names.update(name for name in value.values() if isinstance(name, str) and name)
    return sorted(names, key=str.casefold)


async def state(request: web.Request) -> web.Response:
    cfg = settings.load()
    watcher = request.app["watcher"]
    if cfg["app_mode"] != "open" and _admin(request) is None:
        return web.json_response({
            "now": datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
            "maintenance": {"mode": cfg["app_mode"], "message": cfg["maintenance_message"]},
            "ticker_text": cfg["ticker_text"],
            "alert_map": _alert_map_block(watcher),
        })
    start = _d(request.query["from"]) if "from" in request.query else datetime.now(KYIV_TZ).date()
    n = max(1, min(int(request.query.get("days", "7")), MAX_DAYS))
    return web.json_response({
        "now": datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
        "alert": _alert_block(watcher, admin=_admin(request) is not None),
        "alert_map": _alert_map_block(watcher),
        "days": {(start + timedelta(days=i)).isoformat(): _day_block(start + timedelta(days=i)) for i in range(n)},
        "lesson_names": _lesson_names(),
        "holidays": holidays.list_all(),
        "remote_periods": remote_periods.list_all(),
        "week_shifts": week_shifts.list_all(),
        "settings": settings.load(),
    })


async def _bot_username(app: web.Application) -> str | None:
    if app.get("bot_username"):
        return app["bot_username"]
    try:
        app["bot_username"] = (await app["bot"].get_me()).username
    except Exception:
        return None
    return app["bot_username"]


async def me(request: web.Request) -> web.Response:
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    out = {"admin": bool(user and user.get("id") in config.ADMIN_IDS)}
    if user:
        out["subscription"] = notify.get_topics(int(user["id"]))
        out["bot_username"] = await _bot_username(request.app)
    return web.json_response(out)


async def alert_stats_view(request: web.Request) -> web.Response:
    data = await asyncio.get_running_loop().run_in_executor(None, alert_stats.compute)
    return web.json_response(data)


TOPIC_TITLES = {"alerts": "тривоги та відбій", "changes": "зміни в розкладі"}


async def a_subscribe(request: web.Request) -> web.Response:
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if not user:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    b = await request.json()
    raw = b.get("topics", [])
    if not isinstance(raw, list):
        raise ValueError("topics")
    topics = [t for t in notify.TOPICS if t in raw]
    uid = int(user["id"])
    if set(topics) - set(notify.get_topics(uid)):  # додається нова тема: перевіряємо, що бот може писати
        text = "✅ <b>Особисті сповіщення увімкнено</b>\n" + "\n".join(f"• {TOPIC_TITLES[t]}" for t in topics)
        try:
            await request.app["bot"].send_message(uid, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest):
            raise web.HTTPConflict(text=json.dumps({"error": "start_bot"}), content_type="application/json")
        except Exception:
            raise web.HTTPBadGateway(text=json.dumps({"error": "send_failed"}), content_type="application/json")
    return web.json_response({"ok": True, "topics": notify.set_topics(uid, topics, str(user.get("first_name", "")))})


async def a_backup_send(request: web.Request) -> web.Response:
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    from aiogram.types import BufferedInputFile
    user = _need_admin(request)
    payload, name, names = backup.make_zip()
    try:
        await request.app["bot"].send_document(
            user["id"], BufferedInputFile(payload, filename=name),
            caption=f"🗄 Резервна копія даних бота ({len(names)} файлів). Відновлення: надішліть цей zip боту з підписом /restore")
    except (TelegramForbiddenError, TelegramBadRequest):
        raise web.HTTPConflict(text=json.dumps({"error": "start_bot"}), content_type="application/json")
    return web.json_response({"ok": True, "files": len(names), "bytes": len(payload)})


async def duty_schedule(request: web.Request) -> web.Response:
    if verify_init_data(request.headers.get("X-Init-Data", "")) is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    return web.json_response({
        "configured": bool(config.DUTY_STUDENTS),
        "start_date": config.DUTY_START_DATE,
        "students_list": list(config.DUTY_STUDENTS),
        "days": duties.upcoming(),
    })


async def a_presence(request: web.Request) -> web.Response:
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if user is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")
    body = await request.json()
    page = body.get("page")
    if page not in {"sch", "dut", "al", "adm"}:
        raise ValueError("page")
    activity.record(user, page)
    return web.json_response({"ok": True})


async def a_activity_stats(request: web.Request) -> web.Response:
    _need_admin(request)
    return web.json_response(activity.overview())


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

    if day.weekday() >= 5 or holidays.get_holiday(day) is not None:
        raise ValueError("duty_day")
    if lessons.is_school_week(day) is False:
        raise ValueError("remote_day")

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
    subgroup = b.get("subgroup")
    if subgroup:
        if subgroup not in {"girls", "boys"}:
            raise ValueError("subgroup")
        cancel = bool(b.get("cancel", True))
        cancellations.set_group_cancelled(d, num, subgroup, cancel)
        if cancel:
            remote_lesson_overrides.clear_remote(d, num, subgroup)
        return web.json_response({"ok": True})
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


async def a_remote(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    d, num = _d(b["date"]), int(b["number"])
    subgroup = b.get("subgroup")
    lesson = next((item for item in lessons.get_lessons_for_date(d)
                   if item["number"] == num and (not subgroup or item.get("subgroup") == subgroup)), None)
    if lesson is None:
        raise ValueError("lesson")
    if subgroup:
        if subgroup not in {"girls", "boys"} or not lesson.get("subgroup"):
            raise ValueError("subgroup")
        if b.get("clear"):
            remote_lesson_overrides.clear_remote(d, num, subgroup)
            remote_lesson_overrides.clear_group_in_person(d, num, subgroup)
        elif not b.get("value", True):
            remote_lesson_overrides.clear_remote(d, num, subgroup)
            remote_lesson_overrides.set_group_in_person(d, num, subgroup)
        else:
            cancellations.set_group_cancelled(d, num, subgroup, False)
            remote_lesson_overrides.set_remote(d, num, subgroup)
        return web.json_response({"ok": True})
    if b.get("clear"):
        remote_lesson_overrides.clear_remote(d, num)
        remote_overrides.clear_in_person(d, num)
    elif b.get("value", True):
        remote_lesson_overrides.set_remote(d, num)
        remote_overrides.clear_in_person(d, num)
    else:
        remote_lesson_overrides.clear_remote(d, num)
        if lesson["remote"]:
            remote_overrides.mark_in_person(d, num)
        else:
            remote_overrides.clear_in_person(d, num)
    return web.json_response({"ok": True})


async def a_settings(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    key, value = str(b["key"]), b["value"]
    if key == "schedule_end" and value:
        value = _d(str(value)).isoformat()
    if key == "remote_lesson_links":
        if not isinstance(value, dict) or len(value) > 100:
            raise ValueError("remote_lesson_links")
        clean = {}
        for name, raw_url in value.items():
            name, raw_url = str(name).strip()[:100], str(raw_url).strip()
            if not name or len(raw_url) > 2048:
                raise ValueError("remote_lesson_links")
            parsed = urlsplit(raw_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("remote_lesson_links")
            clean[name] = raw_url
        value = clean
    settings.set_value(key, value)
    return web.json_response({"ok": True, "settings": settings.load()})


async def a_overview(request: web.Request) -> web.Response:
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
                subgroup_label = l.get("subgroup_label")
                changes.append({**base, "subgroup": l.get("subgroup"), "kind": "cancel", "text": f"{subgroup_label}: Класрум" if subgroup_label else "скасовано"})
            if l.get("subgroup") and remote_lesson_overrides.is_group_remote(d, n, l["subgroup"]):
                changes.append({**base, "subgroup": l["subgroup"], "kind": "groupremote", "text": f"{l.get('subgroup_label', l['subgroup'])}: дистанційно"})
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
    if tgt in current:
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


async def a_week_shift(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    if b.get("remove"):
        week_shifts.remove(str(b["remove"]))
    else:
        week_shifts.add(_d(b["from"]), bool(b["school"]))
    return web.json_response({"ok": True, "week_shifts": week_shifts.list_all()})


async def a_announce(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    text = str(b["text"]).strip()[:3500]
    if not text:
        raise ValueError("text")
    await request.app["bot"].send_message(config.CHAT_ID, text)
    return web.json_response({"ok": True})


async def a_report(request: web.Request) -> web.Response:
    user = verify_init_data(request.headers.get("X-Init-Data", ""))
    if user is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "forbidden"}), content_type="application/json")

    user_id = int(user["id"])
    now = _time.time()
    if now - _report_last_sent.get(user_id, 0) < 60:
        raise web.HTTPTooManyRequests(text=json.dumps({"error": "rate_limited"}), content_type="application/json")

    body = await request.json()
    text = str(body.get("text", "")).strip()
    if len(text) < 3 or len(text) > 1000:
        raise ValueError("text")
    page_names = {"sch": "Розклад", "dut": "Чергові", "al": "Тривоги", "adm": "Керування"}
    page = body.get("page", "sch")
    if page not in page_names:
        raise ValueError("page")
    raw_date = str(body.get("date", "")).strip()
    date_line = f"\nДата розкладу: {_d(raw_date).isoformat()}" if raw_date else ""

    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part) or "Користувач"
    username = f" (@{user['username']})" if user.get("username") else ""
    report = f"🐞 Помилка в застосунку\nВід: {name}{username} · ID {user_id}\nРозділ: {page_names[page]}{date_line}\n\n{text}"
    sent = False
    if config.ADMIN_IDS:
        for admin_id in config.ADMIN_IDS:
            try:
                await request.app["bot"].send_message(admin_id, report)
                sent = True
            except Exception:
                log.exception("Не вдалося переслати повідомлення про помилку адміну %s", admin_id)
    if not sent:
        raise web.HTTPServiceUnavailable(text=json.dumps({"error": "admins_unreachable"}), content_type="application/json")

    _report_last_sent[user_id] = now
    return web.json_response({"ok": True})


# --- Керування журналом тривог (за стабільним id запису) ---

def _bad(code: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(text=json.dumps({"error": code}), content_type="application/json")


def _alert_entry(b: dict) -> dict:
    entry = alert_log.get(str(b.get("id", "")))
    if entry is None:
        raise _bad("id")
    return entry


async def a_alert_edit(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    entry = _alert_entry(b)
    ongoing = entry["end"] is None and request.app["watcher"].is_active
    kwargs = {}
    if b.get("start"):
        kwargs["start"] = b["start"]
    if b.get("level"):
        kwargs["level"] = str(b["level"])
    if "is_false" in b:
        kwargs["is_false"] = bool(b["is_false"])
    if "end" in b:
        if ongoing and b["end"]:
            raise _bad("active")  # поточну тривогу закриє відбій від API
        if not ongoing and not b["end"]:
            raise _bad("end")
        if not ongoing:
            kwargs["end"] = b["end"]
    try:
        alert_log.update(entry["id"], **kwargs)
    except ValueError as e:
        raise _bad(str(e))
    return web.json_response({"ok": True})


async def a_alert_false(request: web.Request) -> web.Response:
    _need_admin(request)
    entry = _alert_entry(await request.json())
    return web.json_response({"ok": True, "is_false": alert_log.toggle_false(entry["id"])})


async def a_alert_delete(request: web.Request) -> web.Response:
    _need_admin(request)
    entry = _alert_entry(await request.json())
    if entry["end"] is None and request.app["watcher"].is_active:
        raise _bad("active")
    alert_log.delete(entry["id"])
    return web.json_response({"ok": True})


async def a_alert_clear(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    try:
        removed, kept = alert_log.clear_month(str(b.get("month", "")), keep_open=request.app["watcher"].is_active)
    except ValueError:
        raise _bad("month")
    return web.json_response({"ok": True, "removed": removed, "kept_ongoing": kept})


async def a_alert_add(request: web.Request) -> web.Response:
    _need_admin(request)
    b = await request.json()
    try:
        entry = alert_log.add(b["start"], b["end"], str(b.get("level", "red")), bool(b.get("is_false", False)))
    except ValueError as e:
        raise _bad(str(e))
    return web.json_response({"ok": True, "id": entry["id"]})


def build_app(bot, watcher) -> web.Application:
    app = web.Application(middlewares=[cors])
    app["bot"], app["watcher"] = bot, watcher
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/alert_stats", alert_stats_view)
    app.router.add_post("/api/subscribe", a_subscribe)
    app.router.add_post("/api/admin/backup/send", a_backup_send)
    app.router.add_get("/api/duties", duty_schedule)
    app.router.add_post("/api/activity", a_presence)
    app.router.add_get("/api/admin/activity", a_activity_stats)
    app.router.add_post("/api/admin/duty", a_duty)
    app.router.add_post("/api/admin/cancel", a_cancel)
    app.router.add_post("/api/admin/time", a_time)
    app.router.add_post("/api/admin/lesson", a_lesson)
    app.router.add_post("/api/admin/holiday", a_holiday)
    app.router.add_post("/api/admin/remote_period", a_remote_period)
    app.router.add_post("/api/admin/week_shift", a_week_shift)
    app.router.add_post("/api/admin/endnow", a_endnow)
    app.router.add_post("/api/admin/renumber", a_renumber)
    app.router.add_post("/api/admin/inperson", a_inperson)
    app.router.add_post("/api/admin/remote", a_remote)
    app.router.add_post("/api/admin/settings", a_settings)
    app.router.add_get("/api/admin/overview", a_overview)
    app.router.add_post("/api/admin/announce", a_announce)
    app.router.add_post("/api/report", a_report)

    # Реєстрація нових роутів для тривог
    app.router.add_post("/api/admin/alert/edit", a_alert_edit)
    app.router.add_post("/api/admin/alert/false", a_alert_false)
    app.router.add_post("/api/admin/alert/delete", a_alert_delete)
    app.router.add_post("/api/admin/alert/add", a_alert_add)
    app.router.add_post("/api/admin/alert/clear", a_alert_clear)

    app.router.add_get("/", lambda r: web.Response(text="ok"))
    return app


async def start(bot, watcher) -> web.AppRunner:
    app = build_app(bot, watcher)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("API запущено на порту %s", port)
    return runner
