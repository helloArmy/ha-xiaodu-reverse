"""Shared entity base for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CannotConnect, LoginExpired, XiaoDuApi
from .appliance_types import ApplianceTypes, state_is_on, state_value
from .const import DOMAIN, MANUFACTURER
from .coordinator import XiaoDuCoordinator

_LOGGER = logging.getLogger(__name__)


def get_runtime(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[XiaoDuCoordinator, dict[str, XiaoDuApi]]:
    """Return ``(coordinator, apis)`` stored by ``async_setup_entry``."""
    data = (hass.data.get(DOMAIN) or {}).get(entry.entry_id) or {}
    coordinator = data.get("coordinator")
    apis = data.get("apis") or {}
    if coordinator is None:
        raise HomeAssistantError("小度集成尚未完成初始化")
    return coordinator, apis


def types_for(
    coordinator: XiaoDuCoordinator, api: XiaoDuApi, appliance_id: str
) -> list[str]:
    """Return the appliance type list, preferring live coordinator data."""
    live = coordinator.get_appliance(appliance_id).get("applianceTypes")
    if isinstance(live, list) and live:
        return [str(t) for t in live]
    return [str(t) for t in api.appliance_types]


class XiaoDuEntity(CoordinatorEntity[XiaoDuCoordinator]):
    """Base class binding an entity to one XiaoDu appliance."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
        unique_suffix: str,
        name_suffix: str | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._api = api
        self._appliance_id = appliance_id
        self._attr_unique_id = f"{appliance_id}_{unique_suffix}"
        if name_suffix:
            self._attr_name = name_suffix

    # ── registry / availability ────────────────────────────────────────

    @property
    def appliance(self) -> dict[str, Any]:
        """Return the current appliance payload."""
        return self.coordinator.get_appliance(self._appliance_id)

    @property
    def state_setting(self) -> dict[str, Any]:
        """Return ``stateSetting`` of the appliance."""
        return self.coordinator.get_state_setting(self._appliance_id)

    @property
    def appliance_types(self) -> list[str]:
        """Return the appliance type list."""
        types = self.appliance.get("applianceTypes") or self._api.appliance_types
        return [str(t) for t in types] if isinstance(types, list) else []

    @property
    def device_name(self) -> str:
        """Return the user-visible device name."""
        return str(
            self.appliance.get("friendlyName")
            or self.appliance.get("applianceName")
            or self._appliance_id
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        info = self.appliance
        model = ApplianceTypes.primary_type(self.appliance_types)
        area = info.get("groupName") or info.get("roomName")
        return DeviceInfo(
            identifiers={(DOMAIN, self._appliance_id)},
            name=self.device_name,
            manufacturer=str(info.get("botName") or info.get("brand") or MANUFACTURER),
            model=model,
            suggested_area=str(area) if area else None,
            sw_version=str(info.get("firmwareVersion") or "") or None,
        )

    @property
    def available(self) -> bool:
        """Return True when the coordinator has data for this appliance."""
        return bool(self.appliance) and super().available

    # ── state helpers ──────────────────────────────────────────────────
    #
    # IMPORTANT: these must NOT be named ``state`` / ``is_on``. Every platform
    # class already exposes ``Entity.state`` (a property returning the entity
    # state string) and e.g. ``SwitchEntity.is_on``. Shadowing them with
    # methods makes ``str(self.state)`` return a bound method whose repr
    # touches the entity again, producing infinite __repr__ recursion.

    def attr_value(self, key: str, default: Any = None) -> Any:
        """Read one ``stateSetting`` value."""
        value = state_value(self.state_setting, key)
        return default if value is None else value

    def attr_is_on(self, key: str = "turnOnState") -> bool:
        """Read an on/off attribute."""
        return state_is_on(self.state_setting, key)

    # ── command helper ─────────────────────────────────────────────────

    async def async_send(
        self, awaitable: Any, *, refresh: bool = True
    ) -> None:
        """Run one API command and surface failures as HA errors."""
        try:
            ok, error = await awaitable
        except LoginExpired as exc:
            raise HomeAssistantError(
                f"{self.device_name}: 小度登录已失效，请重新登录"
            ) from exc
        except CannotConnect as exc:
            raise HomeAssistantError(f"{self.device_name}: 无法连接小度云") from exc
        if not ok:
            raise HomeAssistantError(f"{self.device_name}: 控制失败 ({error})")
        if refresh:
            await self.coordinator.async_request_refresh()
