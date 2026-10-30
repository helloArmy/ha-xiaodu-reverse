"""Data update coordinator for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import CannotConnect, LoginExpired, XiaoDuApi
from .const import (
    CONF_BAIDUID,
    CONF_COOKIE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .login import BaiduLogin, BaiduLoginBlocked, BaiduLoginError

_LOGGER = logging.getLogger(__name__)


class XiaoDuCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch state for every selected appliance.

    ``self.data`` maps ``appliance_id`` → the ``appliance`` dict from
    ``appliancedetails`` (``friendlyName`` / ``botName`` / ``groupName`` /
    ``applianceTypes`` / ``stateSetting`` / ``panels``).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        apis: dict[str, XiaoDuApi],
        house_id: str,
        entry: ConfigEntry | None = None,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{house_id}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.apis = apis
        self.house_id = house_id
        self.entry = entry
        self._relogin_attempts = 0
        # Full appliance payloads captured during setup (they carry the
        # ``panels`` definition that entity setup needs, and that the batch
        # polling endpoint sometimes omits).
        self._seed: dict[str, dict[str, Any]] = {}

    def set_seed(self, seed: dict[str, dict[str, Any]]) -> None:
        """Store setup-time appliance payloads as a fallback."""
        self._seed = dict(seed)

    # ── lookup helpers used by entities ────────────────────────────────

    def get_appliance(self, appliance_id: str) -> dict[str, Any]:
        """Return the appliance dict for an id (empty dict when unknown)."""
        if self.data and appliance_id in self.data:
            return self.data[appliance_id] or {}
        return self._seed.get(appliance_id) or {}

    def get_state_setting(self, appliance_id: str) -> dict[str, Any]:
        """Return ``stateSetting`` for an appliance."""
        return self.get_appliance(appliance_id).get("stateSetting") or {}

    def get_panels(self, appliance_id: str) -> list[dict[str, Any]]:
        """Return the ``panels`` definition for an appliance."""
        panels = self.get_appliance(appliance_id).get("panels")
        return panels if isinstance(panels, list) else []

    def api_for(self, appliance_id: str) -> XiaoDuApi | None:
        """Return the API client bound to an appliance."""
        return self.apis.get(appliance_id)

    # ── refresh ────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Batch-poll every appliance, falling back to per-device detail."""
        if not self.apis:
            return {}

        results: dict[str, dict[str, Any]] = {}
        ids = list(self.apis.keys())
        first_api = next(iter(self.apis.values()))

        try:
            batch = await first_api.async_get_appliances_by_ids(self.house_id, ids)
        except LoginExpired:
            await self._async_relogin()
            batch = await first_api.async_get_appliances_by_ids(self.house_id, ids)
        except CannotConnect as exc:
            raise UpdateFailed(f"无法连接小度云: {exc}") from exc

        by_id = {
            str(item.get("applianceId")): item
            for item in batch
            if item.get("applianceId")
        }

        missing: list[str] = []
        for aid in ids:
            info = by_id.get(aid)
            if info and info.get("stateSetting"):
                results[aid] = self._merge_with_cache(aid, info)
            else:
                missing.append(aid)

        # Per-device fallback: some appliances are omitted by the batch
        # endpoint but answered by appliancedetails (hass_xiaodu path).
        for aid in missing:
            api = self.apis.get(aid)
            if api is None:
                continue
            try:
                detail = await api.async_get_detail(aid)
            except LoginExpired:
                await self._async_relogin()
                try:
                    detail = await api.async_get_detail(aid)
                except (LoginExpired, CannotConnect):
                    detail = {}
            except CannotConnect:
                detail = {}
            appliance = detail.get("appliance") if isinstance(detail, dict) else None
            if isinstance(appliance, dict) and appliance:
                results[aid] = self._merge_with_cache(aid, appliance)
            elif self.data and aid in self.data:
                results[aid] = self.data[aid]
            else:
                _LOGGER.debug("设备 %s 本轮无数据", aid)

        if not results:
            raise UpdateFailed("未能获取任何小度设备的状态")

        self._relogin_attempts = 0
        return results

    def _merge_with_cache(
        self, appliance_id: str, fresh: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep previously known fields the batch endpoint may omit.

        ``GET_APPLIANCES_BY_ID`` sometimes returns a trimmed appliance
        (no ``panels``). Entity setup depends on those fields, so they are
        carried over from the last full detail.
        """
        cached = self.get_appliance(appliance_id)
        if not cached:
            return fresh
        merged = dict(fresh)
        for key in ("panels", "applianceTypes", "botName", "groupName", "friendlyName"):
            if not merged.get(key) and cached.get(key):
                merged[key] = cached[key]
        return merged

    async def _async_relogin(self) -> None:
        """Re-login with the stored credentials and push the new BDUSS."""
        entry = self.entry
        if entry is None:
            raise ConfigEntryAuthFailed("小度登录已失效，且没有可用于自动重登的凭据")
        username = entry.data.get(CONF_USERNAME) or ""
        password = entry.data.get(CONF_PASSWORD) or ""
        if not username or not password:
            raise ConfigEntryAuthFailed(
                "小度登录已失效，且未保存账号密码，请重新登录"
            )
        if self._relogin_attempts >= 3:
            raise ConfigEntryAuthFailed("小度自动重新登录连续失败，请手动重新登录")

        self._relogin_attempts += 1
        first_api = next(iter(self.apis.values()))
        client = BaiduLogin(first_api.session)
        try:
            result = await client.login(
                username, password, baiduid=entry.data.get(CONF_BAIDUID) or ""
            )
        except BaiduLoginBlocked as exc:
            _LOGGER.warning("小度自动重登被风控拦截: %s", exc)
            raise ConfigEntryAuthFailed(
                "小度登录失效，自动重登被百度风控要求短信验证，请手动重新登录"
            ) from exc
        except BaiduLoginError as exc:
            _LOGGER.warning("小度自动重登失败: %s", exc)
            raise ConfigEntryAuthFailed(f"小度自动重登失败: {exc}") from exc

        new_cookie = result.get("bduss") or ""
        if not new_cookie:
            raise ConfigEntryAuthFailed("小度自动重登未返回有效凭据")

        for api in self.apis.values():
            api.update_cookie(new_cookie)
        if new_cookie != entry.data.get(CONF_COOKIE):
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_COOKIE: new_cookie}
            )
        _LOGGER.info("小度登录凭据已自动更新")


def build_scan_interval(entry: ConfigEntry) -> int:
    """Return the configured scan interval in seconds."""
    return int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
