"""
Вбудований веб-сервер Mini App (aiohttp). Працює в тому ж процесі, що й бот.

  GET  /                   сторінка застосунку (bot/web/index.html)
  GET  /healthz            перевірка "живий" для хостингу (без авторизації)
  GET  /api/state          розклад по днях + стан тривоги (для учасників чату)
  GET  /api/stats          статистика тривог
  POST /api/admin/...      дії адміна (скасувати урок, змінити час, оголошення...)

Усі /api/* вимагають заголовок  Authorization: tma <initData>  - підписані
Telegram дані користувача. Доступ мають учасники класного чату; /api/admin/*
- тільки ADMIN_IDS. Усе, що змінює дані, використовує ті самі модулі, що й
команди бота (/cancel, /settime ...), тому поведінка однакова.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

import alert_history
import alert_impact
import bell_overrides
import cancellations
import early_end
import extra_lessons
import holidays
import remote_overrides
import settings
from lessons import (
    KYIV_TZ,
    find_current_and_next,
    format_lesson_room,
    get_lesson_by_number,
    get_lessons_for_date,
    is_lesson_remote_today,
    is_school_week,
    visible_room,
)
from webapp_auth import validate_init_data

log = logging.getLogger("webapp")

INDEX_PATH = Path(__file__).parent / "web" / "index.html"
WEEKDAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class WebAppDeps:
    bot_token: str
    chat_id: int | str
    admin_ids: set[int]
    bot: Any  # потрібні send_message() і get_chat_member() (в тестах - заглушка)
    alert_state_fn: Callable[[], dict]  # AlertWatcher.snapshot()
    now_fn: Callable[[], datetime] = lambda: datetime.now(KYIV_TZ)
    region_title: str = ""
    require_membership: bool = True
    auth_fn: Callable[[str], dict | None] | None = None  # для демо-режиму; у бойовому - None
    member_cache_ttl: int = 300
    started_at: float = field(default_factory=time.time)
    version: str = "dev"
    _member_cache: dict = field(default_factory=dict)


DEPS = web.AppKey("deps", WebAppDeps)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


# ---------------- валідація вхідних даних ----------------

def _date(v: Any) -> date:
    try:
        d = date.fromisoformat(str(v))
    except ValueError:
        raise ApiError(400, "Невірна дата (потрібен формат РРРР-ММ-ДД)")
    if abs((d - datetime.now(KYIV_TZ).date()).days) > 400:
        raise ApiError(400, "Дата надто далеко")
    return d


def _int(v: Any, lo: int, hi: int, what: str = "Номер уроку") -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"{what} має бути числом")
    if not lo <= n <= hi:
        raise ApiError(400, f"{what} має бути від {lo} до {hi}")
    return n


def _hhmm(v: Any, what: str = "Час") -> str:
    m = _HHMM.match(str(v or "").strip())
    if not m:
        raise ApiError(400, f"{what} має бути у форматі ЧЧ:ММ")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _text(v: Any, maxlen: int, what: str) -> str:
    s = str(v or "").strip()
    if not s:
        raise ApiError(400, f"{what} не може бути порожнім")
    if len(s) > maxlen:
        raise ApiError(400, f"{what} задовге (максимум {maxlen} символів)")
    return s


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise ApiError(400, "Очікується JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "Очікується JSON-об'єкт")
    return data


# ---------------- доступ ----------------

async def _is_member(deps: WebAppDeps, user_id: int) -> bool:
    if user_id in deps.admin_ids or not deps.require_membership:
        return True
    cached = deps._member_cache.get(user_id)
    now = time.monotonic()
    if cached and now - cached[0] < deps.member_cache_ttl:
        return cached[1]
    try:
        member = await deps.bot.get_chat_member(deps.chat_id, user_id)
        ok = member.status in ("creator", "administrator", "member") or (
            member.status == "restricted" and bool(getattr(member, "is_member", False))
        )
    except Exception as e:
        log.warning("Не вдалося перевірити членство %s: %s", user_id, e)
        ok = False
    deps._member_cache[user_id] = (now, ok)
    return ok


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as e:
        return _json({"error": e.message}, e.status)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("Необроблена помилка в %s", request.path)
        return _json({"error": "Внутрішня помилка сервера"}, 500)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)

    deps: WebAppDeps = request.app[DEPS]
    scheme, _, init_data = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "tma":
        raise ApiError(401, "Відкрийте застосунок через бота в Telegram")

    check = deps.auth_fn or (lambda raw: validate_init_data(raw, deps.bot_token))
    user = check(init_data.strip())
    if user is None:
        raise ApiError(401, "Сесія недійсна або прострочена - перезапустіть застосунок")

    if not await _is_member(deps, user["id"]):
        raise ApiError(403, "Доступ тільки для учасників класного чату")

    request["user"] = user
    request["is_admin"] = user["id"] in deps.admin_ids
    if request.path.startswith("/api/admin/") and not request["is_admin"]:
        raise ApiError(403, "Тільки для адміністраторів")
    return await handler(request)


# ---------------- побудова стану ----------------

def _day_payload(d: date) -> dict:
    school = is_school_week(d)
    extra_numbers = {e["number"] for e in extra_lessons.get_extra_lessons(d)}
    items = []
    for l in get_lessons_for_date(d):
        n = l["number"]
        items.append({
            "number": n,
            "name": l["name"],
            "start": l["start"],
            "end": l["end"],
            "room": visible_room(l, d),
            "remote": is_lesson_remote_today(l, d),
            "remote_eligible": bool(l["remote"]) and school is not False,
            "cancelled": l["cancelled"],
            "cancelled_label": (("Класрум" if school is False else "скасовано") if l["cancelled"] else None),
            "early_end": early_end.get_early_end(d, n),
            "in_person_confirmed": remote_overrides.is_in_person(d, n),
            "extra": n in extra_numbers,
            "bell_override": bell_overrides.get_override(d, n) is not None,
        })
    return {
        "date": d.isoformat(),
        "holiday": holidays.holiday_on(d),
        "weekday": d.weekday(),
        "weekday_name": WEEKDAYS[d.weekday()],
        "week_type": None if school is None else ("school" if school else "home"),
        "lessons": items,
    }


async def state_handler(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    now = deps.now_fn()
    today = now.date()

    if "from" in request.query:
        start = _date(request.query["from"])
        days = _int(request.query.get("days", 14), 1, 28, "Кількість днів")
    else:  # за замовчуванням: минулий + поточний + наступний тиждень
        start = today - timedelta(days=today.weekday() + 7)
        days = 21

    snap = deps.alert_state_fn() or {}
    active = bool(snap.get("active"))
    payload = {
        "now": now.isoformat(),
        "now_secs": now.hour * 3600 + now.minute * 60 + now.second,
        "today": today.isoformat(),
        "is_admin": request["is_admin"],
        "user": {"id": request["user"]["id"], "first_name": request["user"].get("first_name")},
        "region": deps.region_title,
        "alert": {
            "active": active,
            "level": snap.get("level") if active else None,
            "threats": snap.get("threats", []) if active else [],
            "notes": snap.get("notes") if active else None,
            "since": alert_history.open_since() if active else None,
            "last_ended": _last_ended(),
            "data_age_seconds": snap.get("data_age_seconds"),
            "stale": bool(snap.get("stale", False)),
            "last_error": snap.get("last_error"),
            "nearby": snap.get("nearby", []),
        },
        "holidays": holidays.upcoming(today),
        "days": [_day_payload(start + timedelta(days=i)) for i in range(days)],
    }
    if request["is_admin"]:
        payload["settings"] = settings.all()
    return _json(payload)


def _last_ended() -> dict | None:
    for sess in reversed(alert_history.sessions()):
        if sess["end"]:
            return {"start": sess["start"].isoformat(), "end": sess["end"].isoformat(), "level": sess["level"],
                    "duration_seconds": int((sess["end"] - sess["start"]).total_seconds())}
    return None


async def stats_handler(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    now = deps.now_fn()
    st = alert_history.stats(now)
    sess = alert_history.sessions()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    st["week"].update(alert_impact.window_impact(sess, week_start, now))
    st["month"].update(alert_impact.window_impact(sess, month_start, now))
    for item, sobj in zip(st["recent"], reversed(sess[-10:])):
        imp = alert_impact.session_impact(sobj["start"], sobj["end"], now)
        item["lost_minutes"] = imp["minutes"]
        item["lessons"] = imp["lessons"]
    return _json(st)


# ---------------- дії адміна ----------------

def _require_lesson(d: date, number: int) -> dict:
    lesson = get_lesson_by_number(number, d)
    if lesson is None:
        raise ApiError(404, f"{number}-го уроку немає в розкладі на {d.isoformat()}")
    return lesson


def _ok() -> web.Response:
    return _json({"ok": True})


async def admin_cancel(request: web.Request) -> web.Response:
    b = await _body(request)
    d, n = _date(b.get("date")), _int(b.get("number"), 0, 30)
    _require_lesson(d, n)
    if b.get("value", True):
        cancellations.cancel_lesson(d, n)
    else:
        cancellations.uncancel_lesson(d, n)
    return _ok()


async def admin_settime(request: web.Request) -> web.Response:
    b = await _body(request)
    d, n = _date(b.get("date")), _int(b.get("number"), 0, 30)
    start, end = _hhmm(b.get("start"), "Початок"), _hhmm(b.get("end"), "Кінець")
    if start >= end:
        raise ApiError(400, "Початок має бути раніше за кінець")
    _require_lesson(d, n)
    bell_overrides.set_override(d, n, start, end)
    return _ok()


async def admin_resettime(request: web.Request) -> web.Response:
    b = await _body(request)
    bell_overrides.clear_override(_date(b.get("date")), _int(b.get("number"), 0, 30))
    return _ok()


async def admin_endnow(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    b = await _body(request)
    now = deps.now_fn()
    if b.get("number") is not None:
        lesson = _require_lesson(now.date(), _int(b["number"], 0, 30))
    else:
        lesson, _, _, _ = find_current_and_next(now)
        if lesson is None:
            raise ApiError(409, "Зараз немає активного уроку")
    early_end.mark_early_end(now.date(), lesson["number"], now.strftime("%H:%M"))
    return _ok()


async def admin_undoend(request: web.Request) -> web.Response:
    b = await _body(request)
    early_end.clear_early_end(_date(b.get("date")), _int(b.get("number"), 0, 30))
    return _ok()


async def admin_addlesson(request: web.Request) -> web.Response:
    b = await _body(request)
    d, n = _date(b.get("date")), _int(b.get("number"), 0, 30)
    name = _text(b.get("name"), 60, "Назва уроку")
    start, end = _hhmm(b.get("start"), "Початок"), _hhmm(b.get("end"), "Кінець")
    if start >= end:
        raise ApiError(400, "Початок має бути раніше за кінець")
    extra_lessons.add_extra_lesson(d, n, name, start, end)
    return _ok()


async def admin_removelesson(request: web.Request) -> web.Response:
    b = await _body(request)
    if not extra_lessons.remove_extra_lesson(_date(b.get("date")), _int(b.get("number"), 0, 30)):
        raise ApiError(404, "Такого доданого уроку немає")
    return _ok()


async def admin_inperson(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    b = await _body(request)
    d, n = _date(b.get("date")), _int(b.get("number"), 0, 30)
    _require_lesson(d, n)
    if not b.get("value", True):
        remote_overrides.clear_in_person(d, n)
        return _ok()

    remote_overrides.mark_in_person(d, n)
    if b.get("announce", True) and d == deps.now_fn().date():
        fresh = get_lesson_by_number(n, d)
        text = f"📢 Сьогодні {n}-й урок «{fresh['name']}» буде ОЧНО{format_lesson_room(fresh, d)}"
        try:
            await deps.bot.send_message(deps.chat_id, text)
        except Exception as e:
            log.warning("Не вдалося надіслати оголошення про очний урок: %s", e)
    return _ok()


async def admin_announce(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    b = await _body(request)
    text = _text(b.get("text"), 1000, "Текст оголошення")
    try:
        await deps.bot.send_message(deps.chat_id, f"📢 {text}")
    except Exception as e:
        log.warning("Не вдалося надіслати оголошення: %s", e)
        raise ApiError(502, "Не вдалося надіслати в чат")
    return _ok()


async def admin_settings(request: web.Request) -> web.Response:
    b = await _body(request)
    changes = b.get("settings", b)
    if not isinstance(changes, dict) or not changes:
        raise ApiError(400, "Немає що змінювати")
    try:
        new = settings.set_many(changes)
    except ValueError as e:
        raise ApiError(400, str(e))
    return _json({"ok": True, "settings": new})


async def admin_weektype(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    b = await _body(request)
    value = b.get("value")
    if value not in ("school", "home"):
        raise ApiError(400, "value має бути school або home")
    d = _date(b["date"]) if b.get("date") else deps.now_fn().date()
    monday = d - timedelta(days=d.weekday())
    anchor = monday if value == "school" else monday - timedelta(days=7)
    settings.set_many({"school_week_anchor": anchor.isoformat()})
    return _ok()


async def admin_holiday_add(request: web.Request) -> web.Response:
    b = await _body(request)
    start, end = _date(b.get("start")), _date(b.get("end"))
    name = str(b.get("name") or "").strip()
    if len(name) > 60:
        raise ApiError(400, "Назва задовга (максимум 60 символів)")
    after = b.get("after") or None
    if after not in (None, "home", "school"):
        raise ApiError(400, "after має бути home або school")
    weeks = _int(b.get("after_weeks", 1), 1, 8, "Кількість тижнів")
    try:
        holidays.add(start, end, name, after, weeks)
    except ValueError as e:
        raise ApiError(400, str(e))
    return _ok()


async def admin_holiday_remove(request: web.Request) -> web.Response:
    b = await _body(request)
    if not holidays.remove(_date(b.get("start"))):
        raise ApiError(404, "Таких канікул немає")
    return _ok()


async def admin_testmsg(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    try:
        await deps.bot.send_message(deps.chat_id, "✅ Тестове повідомлення: бот і Mini App працюють")
    except Exception as e:
        log.warning("Тестове повідомлення не надіслано: %s", e)
        raise ApiError(502, "Не вдалося надіслати в чат (чи бот у групі й має право писати?)")
    return _ok()


async def admin_diag(request: web.Request) -> web.Response:
    deps: WebAppDeps = request.app[DEPS]
    snap = deps.alert_state_fn() or {}
    files = {}
    try:
        files = {f.name: f.stat().st_size for f in sorted(settings.DATA_DIR.glob("*.json"))}
    except OSError:
        pass
    return _json({
        "uptime_seconds": int(time.time() - deps.started_at),
        "version": deps.version,
        "data_dir": str(settings.DATA_DIR),
        "files": files,
        "alert_api": {"data_age_seconds": snap.get("data_age_seconds"), "stale": bool(snap.get("stale", False)),
                      "last_error": snap.get("last_error")},
    })


# ---------------- статика і збірка застосунку ----------------

async def index_handler(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(INDEX_PATH, headers={"Cache-Control": "no-store"})


async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _security_headers(request: web.Request, response: web.StreamResponse) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"


def create_app(deps: WebAppDeps) -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware], client_max_size=64 * 1024)
    app[DEPS] = deps
    app.on_response_prepare.append(_security_headers)

    app.router.add_get("/", index_handler)
    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/api/state", state_handler)
    app.router.add_get("/api/stats", stats_handler)
    app.router.add_get("/api/admin/diag", admin_diag)
    for name, handler in {
        "cancel": admin_cancel, "settime": admin_settime, "resettime": admin_resettime,
        "endnow": admin_endnow, "undoend": admin_undoend, "addlesson": admin_addlesson,
        "removelesson": admin_removelesson, "inperson": admin_inperson, "announce": admin_announce,
        "settings": admin_settings, "weektype": admin_weektype, "testmsg": admin_testmsg,
        "holiday/add": admin_holiday_add, "holiday/remove": admin_holiday_remove,
    }.items():
        app.router.add_post(f"/api/admin/{name}", handler)
    return app


async def start_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("Mini App слухає %s:%s", host, port)
    return runner
