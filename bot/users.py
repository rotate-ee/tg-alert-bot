"""Реєстр користувачів бота: хто запускав (/start), кому можна писати в ЛС, статистика запусків."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import notify

KYIV_TZ = ZoneInfo("Europe/Kyiv")
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "users.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
MAX_EVENTS = 5000


def _now() -> datetime:
    return datetime.now(KYIV_TZ)


def _load() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("users", {})
            data.setdefault("events", [])
            return data
    except (OSError, ValueError):
        pass
    return {"users": {}, "events": []}


def _save(data: dict) -> None:
    data["events"] = data["events"][-MAX_EVENTS:]
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _name(user: dict) -> str:
    full = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    return (full or user.get("username") or "Користувач Telegram")[:60]


def record_start(user: dict) -> bool:
    """Фіксує запуск бота (/start). True - це перший запуск цього користувача."""
    data, now = _load(), _now().isoformat(timespec="seconds")
    uid = str(user["id"])
    entry = data["users"].get(uid)
    is_new = entry is None or entry.get("starts", 0) == 0
    entry = entry or {"first": now, "starts": 0}
    entry.update(name=_name(user), username=user.get("username") or "", last=now, blocked=False)
    entry["starts"] = entry.get("starts", 0) + 1
    data["users"][uid] = entry
    data["events"].append({"t": now, "uid": int(uid), "new": is_new})
    _save(data)
    return is_new


def mark_reachable(user: dict) -> None:
    """Користувачу вдалося написати (напр., підтвердження підписки): додаємо в реєстр без події /start."""
    data = _load()
    uid = str(user["id"])
    entry = data["users"].setdefault(uid, {"first": _now().isoformat(timespec="seconds"), "starts": 0})
    entry.update(name=_name(user), username=user.get("username") or "", blocked=False)
    _save(data)


def set_blocked(user_id: int, blocked: bool) -> None:
    data = _load()
    entry = data["users"].get(str(user_id))
    if entry and entry.get("blocked") != blocked:
        entry["blocked"] = blocked
        _save(data)


def seed_from_activity() -> int:
    """Одноразово додає тих, хто відкривав застосунок до появи реєстру (їм можливо ще не можна писати)."""
    import activity
    data = _load()
    if data.get("seeded"):
        return 0
    added = 0
    for uid, info in activity._load().get("users", {}).items():
        if uid not in data["users"]:
            data["users"][uid] = {"first": info.get("last_seen", _now().isoformat(timespec="seconds")), "starts": 0,
                                  "blocked": False, "source": "app", "name": info.get("name", ""),
                                  "username": info.get("username", "")}
            added += 1
    data["seeded"] = True
    _save(data)
    return added


def audience_ids(kind: str = "all") -> list[int]:
    """Кому можна розсилати: 'all' - усі, хто не заблокував бота; 'subscribers' - лише підписники сповіщень."""
    users = _load()["users"]
    ids = [int(uid) for uid, u in users.items() if not u.get("blocked")]
    if kind == "subscribers":
        subs = set(notify.subscribers("alerts")) | set(notify.subscribers("changes"))
        ids = [i for i in ids if i in subs]
    return ids


def stats(now: datetime | None = None) -> dict:
    now = now or _now()
    data = _load()
    users, events = data["users"], data["events"]
    parse = datetime.fromisoformat
    d7, d30 = now - timedelta(days=7), now - timedelta(days=30)
    count = lambda since, only_new=False: sum(1 for e in events if parse(e["t"]) >= since and (e["new"] or not only_new))
    by_day = {}
    for e in events:
        key = e["t"][:10]
        by_day.setdefault(key, [0, 0])
        by_day[key][0] += 1
        by_day[key][1] += 1 if e["new"] else 0
    days = []
    for i in range(29, -1, -1):
        d = (now.date() - timedelta(days=i)).isoformat()
        days.append({"date": d, "starts": by_day.get(d, [0, 0])[0], "new": by_day.get(d, [0, 0])[1]})
    recent = []
    for e in reversed(events[-8:]):
        u = users.get(str(e["uid"]), {})
        recent.append({"name": u.get("name") or "—", "username": u.get("username", ""), "at": e["t"], "new": e["new"]})
    blocked = sum(1 for u in users.values() if u.get("blocked"))
    return {
        "totals": {
            "known": len(users), "started": sum(1 for u in users.values() if u.get("starts", 0) > 0),
            "starts_total": sum(u.get("starts", 0) for u in users.values()),
            "blocked": blocked, "reachable": len(users) - blocked,
            "starts_7": count(d7), "starts_30": count(d30), "new_7": count(d7, True), "new_30": count(d30, True),
        },
        "by_day": days,
        "recent": recent,
    }
