"""
Опрос публичного API alerts.in.ua и определение момента,
когда воздушная тревога в заданной области заканчивается ("отбой").

Токен получить бесплатно: https://alerts.in.ua/ -> "Отримати токен API".
Документация: https://api.alerts.in.ua/
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

import alert_log

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
ALERT_TYPE = "air_raid"  # нас интересует именно воздушная тревога

log = logging.getLogger("alerts")


class AlertWatcher:
    """
    Хранит последнее известное состояние тревоги для региона (активна/нет
    и жовтий/червоний рівень) и умеет сказать, произошёл ли переход
    "была тревога -> отбой", "почалась" чи "рівень змінився".
    """

    def __init__(self, token: str, region_title: str):
        self.token = token
        self.region_title = region_title
        self._was_active = False  # состояние с предыдущего опроса
        self._level: str | None = None  # "red" | "yellow" | None
        self.last_ok: datetime | None = None  # час останнього успішного опитування
        self.active_alerts: list[dict] = []

    @property
    def is_active(self) -> bool:
        """Чи триває тривога зараз (за останнім опитуванням)."""
        return self._was_active

    @property
    def level(self) -> str | None:
        """Поточний рівень тривоги ("red"/"yellow") або None, якщо тривоги немає."""
        return self._level

    async def _fetch_active_alerts(self, session: aiohttp.ClientSession) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.get(API_URL, headers=headers, timeout=15) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("alerts", [])

    def _find_region_air_raid(self, alerts: list[dict]) -> dict | None:
        for alert in alerts:
            if (
                alert.get("alert_type") == ALERT_TYPE
                and self.region_title.lower() in (alert.get("location_title") or "").lower()
            ):
                return alert
        return None

    async def poll_once(self, session: aiohttp.ClientSession) -> str | None:
        """
        Делает один запрос к API и возвращает:
          "started"       - если тревога только что началась,
          "ended"         - если тревога только что закончилась (відбій),
          "level_changed" - тривога триває, але рівень (жовтий/червоний) змінився,
          None            - якщо нічого не змінилось.
        Після виклику актуальний рівень доступний через .level.
        """
        try:
            alerts = await self._fetch_active_alerts(session)
        except Exception as e:
            log.warning("Не удалось получить статус тревоги: %s", e)
            return None

        self.last_ok = datetime.now(ZoneInfo("Europe/Kyiv"))
        self.active_alerts = alerts
        region_alert = self._find_region_air_raid(alerts)
        is_active = region_alert is not None
        new_level = region_alert.get("alert_level") if region_alert else None

        event = None
        if is_active and not self._was_active:
            event = "started"
        elif not is_active and self._was_active:
            event = "ended"
        elif is_active and self._was_active and new_level != self._level:
            event = "level_changed"

        if event == "started":
            alert_log.on_started(new_level)
        elif event == "ended":
            alert_log.on_ended()
        elif event == "level_changed":
            alert_log.on_level_changed(new_level)

        self._was_active = is_active
        self._level = new_level
        return event
