"""
Опрос публичного API alerts.in.ua и определение момента,
когда воздушная тревога в заданной области заканчивается ("отбой").

Токен получить бесплатно: https://alerts.in.ua/ -> "Отримати токен API".
Документация: https://api.alerts.in.ua/
"""
import logging
import aiohttp

API_URL = "https://api.alerts.in.ua/v1/alerts/active.json"
ALERT_TYPE = "air_raid"  # нас интересует именно воздушная тревога

log = logging.getLogger("alerts")


class AlertWatcher:
    """
    Хранит последнее известное состояние тревоги для региона
    и умеет сказать, произошёл ли переход "была тревога -> отбой".
    """

    def __init__(self, token: str, region_title: str):
        self.token = token
        self.region_title = region_title
        self._was_active = False  # состояние с предыдущего опроса

    async def _fetch_active_alerts(self, session: aiohttp.ClientSession) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with session.get(API_URL, headers=headers, timeout=15) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("alerts", [])

    def _region_has_air_raid(self, alerts: list[dict]) -> bool:
        for alert in alerts:
            if (
                alert.get("alert_type") == ALERT_TYPE
                and self.region_title.lower() in (alert.get("location_title") or "").lower()
            ):
                return True
        return False

    async def poll_once(self, session: aiohttp.ClientSession) -> str | None:
        """
        Делает один запрос к API и возвращает:
          "started" - если тревога только что началась,
          "ended"   - если тревога только что закончилась (это и есть "отбой"),
          None      - если состояние не изменилось.
        """
        try:
            alerts = await self._fetch_active_alerts(session)
        except Exception as e:
            log.warning("Не удалось получить статус тревоги: %s", e)
            return None

        is_active = self._region_has_air_raid(alerts)

        event = None
        if is_active and not self._was_active:
            event = "started"
        elif not is_active and self._was_active:
            event = "ended"

        self._was_active = is_active
        return event
