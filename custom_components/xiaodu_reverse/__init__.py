"""XiaoDu Reverse (小度反向接入) integration for Home Assistant.

Brings the appliances that live in the XiaoDu / Baidu smart-home cloud back
into Home Assistant:

* login  — wappass account + password (RockJesus/ha-xiaodu)
* control — XiaoDu cloud directives incl. panel pass-through
  (2331892928/hass_xiaodu)
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CannotConnect, LoginExpired, XiaoDuApi
from .const import (
    CONF_APPLIANCE_TYPES,
    CONF_COOKIE,
    CONF_DEVICES,
    CONF_HOUSE_ID,
    CONF_HOUSE_NAME,
    DOMAIN,
)
from .coordinator import build_scan_interval, XiaoDuCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.LOCK,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.VACUUM,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

XiaoDuConfigEntry = ConfigEntry[XiaoDuCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration (config-flow only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def _async_fetch_appliance_details(
    apis: dict[str, XiaoDuApi],
) -> dict[str, dict]:
    """Fetch the full appliance payload for every device (parallel)."""

    async def _one(appliance_id: str, api: XiaoDuApi):
        try:
            detail = await api.async_get_detail(appliance_id)
        except (LoginExpired, CannotConnect) as exc:
            _LOGGER.debug("初始化读取设备 %s 失败: %s", appliance_id, exc)
            return appliance_id, {}
        appliance = detail.get("appliance") if isinstance(detail, dict) else None
        return appliance_id, appliance if isinstance(appliance, dict) else {}

    pairs = await asyncio.gather(
        *(_one(aid, api) for aid, api in apis.items()), return_exceptions=True
    )
    seed: dict[str, dict] = {}
    for pair in pairs:
        if isinstance(pair, Exception):
            continue
        appliance_id, appliance = pair
        if appliance:
            seed[appliance_id] = appliance
    return seed


async def async_setup_entry(hass: HomeAssistant, entry: XiaoDuConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Per-entry client session: the shared session's cookie jar is global, and
    # Baidu cookies must not leak into other integrations.
    session = async_create_clientsession(hass)

    cookie = entry.data.get(CONF_COOKIE, "")
    house_id = entry.data.get(CONF_HOUSE_ID, "")
    devices = entry.data.get(CONF_DEVICES) or []
    appliance_types = entry.data.get(CONF_APPLIANCE_TYPES) or []

    apis: dict[str, XiaoDuApi] = {}
    for index, device in enumerate(devices):
        appliance_id = str(device.get("applianceId") or "")
        if not appliance_id:
            continue
        api = XiaoDuApi(
            cookie=cookie,
            session=session,
            house_id=house_id,
            appliance_id=appliance_id,
        )
        if index < len(appliance_types):
            types = appliance_types[index].get("applianceTypes") or []
            api.appliance_types = [str(t) for t in types]
        apis[appliance_id] = api

    if not apis:
        _LOGGER.error("小度集成未配置任何设备，条目 %s 无法启动", entry.entry_id)
        return False

    seed = await _async_fetch_appliance_details(apis)

    coordinator = XiaoDuCoordinator(
        hass,
        apis,
        house_id=house_id,
        entry=entry,
        scan_interval=build_scan_interval(entry),
    )
    coordinator.set_seed(seed)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "apis": apis,
        "coordinator": coordinator,
        "session": session,
        "house_id": house_id,
        "house_name": entry.data.get(CONF_HOUSE_NAME, ""),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # 临时：探测 HA 容器能否运行 Chromium（结果写入日志，见 _probe.py）
    try:
        from ._probe import async_probe_once

        hass.async_create_task(
            async_probe_once(hass, session), "xiaodu_reverse_probe"
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("XD-PROBE 无法启动探测任务: %s", exc)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: XiaoDuConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # The session is created via async_create_clientsession and owned by
        # Home Assistant — never close it manually here.
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older entry versions."""
    if entry.version > 1:
        _LOGGER.error("配置条目版本 %s 高于本集成支持的版本", entry.version)
        return False
    return True
