"""Short-lived presence and aggregated mini-app usage statistics."""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "activity.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
KYIV_TZ = ZoneInfo("Europe/Kyiv")
ONLINE_SECONDS = 90
SESSION_GAP_SECONDS = 30 * 60
RETENTION_DAYS = 30


def _now() -> datetime:
    return datetime.now(KYIV_TZ)


def _load() -> dict:
    if not DATA_PATH.exists():
        return {"users": {}, "days": {}}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("users", {})
            data.setdefault("days", {})
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"users": {}, "days": {}}


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record(user: dict, page: str) -> None:
    """Record a signed-in Telegram user heartbeat and aggregate page visits."""
    now = _now()
    uid = str(user["id"])
    key = now.date().isoformat()
    data = _load()
    users = data["users"]
    previous = users.get(uid, {})
    previous_seen = previous.get("last_seen")
    last_seen = datetime.fromisoformat(previous_seen) if previous_seen else None
    new_session = (not last_seen or last_seen.date() != now.date()
                   or (now - last_seen).total_seconds() > SESSION_GAP_SECONDS)

    day = data["days"].setdefault(key, {"users": [], "sessions": 0, "views": {}, "visits": []})
    day.setdefault("visits", [])
    if uid not in day["users"]:
        day["users"].append(uid)
    if new_session:
        day["sessions"] += 1
    if new_session or previous.get("page") != page:
        day["views"][page] = day["views"].get(page, 0) + 1

    first = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    name = first or user.get("username") or "Користувач Telegram"
    visits = day["visits"]
    visit = next((v for v in reversed(visits) if v.get("user_id") == uid), None)
    if new_session or visit is None:
        visit = {
            "user_id": uid,
            "name": name,
            "username": user.get("username") or "",
            "page": page,
            "entered_at": now.isoformat(timespec="seconds"),
            "last_seen": now.isoformat(timespec="seconds"),
        }
        visits.append(visit)
    else:
        visit.update({
            "name": name,
            "username": user.get("username") or "",
            "page": page,
            "last_seen": now.isoformat(timespec="seconds"),
        })
    users[uid] = {
        "name": name,
        "username": user.get("username") or "",
        "page": page,
        "last_seen": now.isoformat(timespec="seconds"),
    }

    keep_after = now.date() - timedelta(days=RETENTION_DAYS - 1)
    data["days"] = {d: v for d, v in data["days"].items() if date.fromisoformat(d) >= keep_after}
    user_keep_after = now - timedelta(days=RETENTION_DAYS)
    data["users"] = {
        key_: value for key_, value in users.items()
        if datetime.fromisoformat(value["last_seen"]) >= user_keep_after
    }
    _save(data)


def overview() -> dict:
    now = _now()
    today = now.date()
    data = _load()
    active = []
    for uid, user in data["users"].items():
        seen = datetime.fromisoformat(user["last_seen"])
        seconds = max(0, int((now - seen).total_seconds()))
        if seconds <= ONLINE_SECONDS:
            active.append({
                "name": user["name"],
                "username": user["username"],
                "page": user["page"],
                "seconds_ago": seconds,
            })
    active.sort(key=lambda x: x["seconds_ago"])

    today_data = data["days"].get(today.isoformat(), {})
    week_days = [data["days"].get((today - timedelta(days=i)).isoformat(), {}) for i in range(7)]
    month_days = [data["days"].get((today - timedelta(days=i)).isoformat(), {}) for i in range(RETENTION_DAYS)]
    week_users = {uid for d in week_days for uid in d.get("users", [])}
    month_users = {uid for d in month_days for uid in d.get("users", [])}
    return {
        "active": active,
        "today_visits": list(reversed(today_data.get("visits", []))),
        "stats": {
            "active_now": len(active),
            "today_unique": len(today_data.get("users", [])),
            "today_sessions": today_data.get("sessions", 0),
            "today_views": today_data.get("views", {}),
            "today_page_views": sum(today_data.get("views", {}).values()),
            "week_unique": len(week_users),
            "month_unique": len(month_users),
        },
        "online_window_seconds": ONLINE_SECONDS,
        "updated_at": now.isoformat(timespec="seconds"),
    }
