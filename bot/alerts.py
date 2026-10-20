"""
Опитування API alerts.in.ua і визначення стану тривоги для нашого регіону.

"Наша" тривога - це:
  * запис з назвою нашого району/міста (REGION_TITLE / REGION_EXTRA_TITLES), АБО
  * тривога на ВСЮ область, до якої належить район (REGION_PARENT_TITLE).
Якщо збігів кілька - беремо найвищий рівень (червоний > жовтий).

Ліміти API: жорсткий - 12 запитів/хв з IP; при 429 робимо паузу, щоб не
отримати бан токена. Документація: https://devs.alerts.in.ua/
"""
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
ALERT_TYPE = "air_raid"
KYIV_TZ = ZoneInfo("Europe/Kyiv")
_SEVERITY = {None: 0, "yellow": 1, "red": 2}

THREAT_LABELS = {
    "drones": "Дрони",
    "ballistic_missiles": "Балістика",
    "cruise_missiles": "Крилаті ракети",
    "unspecified_missiles": "Ракетна небезпека",
    "tactic_aircraft_activity": "Тактична авіація",
    "strategic_aircraft_activity": "Стратегічна авіація",
    "mig31k_departure": "Зліт МіГ-31К",
    "guided_aerial_bombs": "КАБи",
    "air_defense": "Робота ППО",
    "unknown": "Інша загроза",
}

log = logging.getLogger("alerts")


def threat_label(threat_type: str | None) -> str:
    return THREAT_LABELS.get(threat_type or "unknown", THREAT_LABELS["unknown"])


def _norm(s) -> str:
    return (s or "").strip().lower()


def match_alert(alert: dict, region_title: str, parent_title: str = "", extra_titles=()) -> bool:
    """Чи стосується цей запис API нашого регіону."""
    if alert.get("alert_type") != ALERT_TYPE:
        return False
    title = _norm(alert.get("location_title"))
    if region_title and _norm(region_title) in title:
        return True
    if any(_norm(e) and _norm(e) in title for e in extra_titles):
        return True
    # тривога на всю область накриває і наш район
    return bool(parent_title) and alert.get("location_type") == "oblast" and title == _norm(parent_title)


def _max_level(levels) -> str | None:
    best = None
    for lv in levels:
        if _SEVERITY.get(lv, 0) > _SEVERITY.get(best, 0):
            best = lv
    return best


class AlertWatcher:
    """Пам'ятає останній стан (є тривога? який рівень? які загрози?) і каже, що змінилось."""

    def __init__(self, token: str, region_title: str, parent_title: str = "",
                 extra_titles=(), nearby_oblasts=(), stale_after: int = 120):
        self.token = token
        self.region_title = region_title
        self.parent_title = parent_title
        self.extra_titles = list(extra_titles)
        self.nearby_oblasts = list(nearby_oblasts)
        self.stale_after = stale_after
        self._was_active = False
        self._level: str | None = None
        self._threats: list[dict] = []
        self._notes: str | None = None
        self.nearby: list[dict] = []
        self.last_ok_at: datetime | None = None
        self.last_error: str | None = None
        self._cooldown_until = 0.0

    # --- стан ---
    @property
    def is_active(self) -> bool:
        return self._was_active

    @property
    def level(self) -> str | None:
        return self._level

    @property
    def threats(self) -> list[dict]:
        return list(self._threats)

    def data_age_seconds(self) -> int | None:
        if self.last_ok_at is None:
            return None
        return max(0, int((datetime.now(KYIV_TZ) - self.last_ok_at).total_seconds()))

    def snapshot(self) -> dict:
        age = self.data_age_seconds()
        return {
            "active": self._was_active,
            "level": self._level if self._was_active else None,
            "threats": self.threats if self._was_active else [],
            "notes": self._notes if self._was_active else None,
            "data_age_seconds": age,
            "stale": age is None or age > self.stale_after,
            "last_error": self.last_error,
            "nearby": list(self.nearby),
        }

    # --- мережа ---
    async def _fetch_active_alerts(self, session: aiohttp.ClientSession) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.get(API_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 429:
                self._cooldown_until = time.monotonic() + 120
                raise RuntimeError("HTTP 429 - перевищено ліміт запитів, пауза 2 хв")
            if resp.status in (401, 403):
                raise RuntimeError(f"HTTP {resp.status} - токен alerts.in.ua недійсний або доступ заблоковано")
            resp.raise_for_status()
            data = await resp.json()
            return data.get("alerts", [])

    def _find_matches(self, alerts: list[dict]) -> list[dict]:
        return [a for a in alerts if match_alert(a, self.region_title, self.parent_title, self.extra_titles)]

    def _build_nearby(self, alerts: list[dict], matched: list[dict]) -> list[dict]:
        wanted = set(self.nearby_oblasts) | ({self.parent_title} if self.parent_title else set())
        matched_ids = {id(a) for a in matched}
        items = []
        for a in alerts:
            if id(a) in matched_ids or a.get("alert_type") != ALERT_TYPE:
                continue
            if a.get("location_oblast") in wanted or a.get("location_title") in wanted:
                items.append({"title": a.get("location_title"), "type": a.get("location_type"),
                              "level": a.get("alert_level")})
        items.sort(key=lambda i: -_SEVERITY.get(i["level"], 0))
        return items[:12]

    @staticmethod
    def _collect_threats(matched: list[dict]) -> list[dict]:
        by_type: dict[str, dict] = {}
        for a in matched:
            for t in a.get("threats") or []:
                key = t.get("threat_type") or "unknown"
                if key not in by_type or _SEVERITY.get(t.get("level"), 0) > _SEVERITY.get(by_type[key]["level"], 0):
                    by_type[key] = {"type": key, "label": threat_label(key),
                                    "level": t.get("level"), "started_at": t.get("started_at")}
        return sorted(by_type.values(), key=lambda t: -_SEVERITY.get(t["level"], 0))

    async def poll_once(self, session: aiohttp.ClientSession) -> str | None:
        """
        "started" | "ended" | "level_changed" | None (без змін або помилка).
        Після виклику актуальні: .level, .threats, .snapshot().
        """
        if time.monotonic() < self._cooldown_until:
            return None
        try:
            alerts = await self._fetch_active_alerts(session)
        except Exception as e:
            self.last_error = str(e) or type(e).__name__
            log.warning("Не вдалося отримати статус тривоги: %s", self.last_error)
            return None

        self.last_ok_at = datetime.now(KYIV_TZ)
        self.last_error = None
        matched = self._find_matches(alerts)
        is_active = bool(matched)
        new_level = _max_level(a.get("alert_level") for a in matched)
        self.nearby = self._build_nearby(alerts, matched)

        event = None
        if is_active and not self._was_active:
            event = "started"
        elif not is_active and self._was_active:
            event = "ended"
        elif is_active and new_level != self._level:
            event = "level_changed"

        self._was_active = is_active
        self._level = new_level if is_active else None
        self._threats = self._collect_threats(matched) if is_active else []
        self._notes = next((a.get("notes") for a in matched if a.get("notes")), None)
        return event
