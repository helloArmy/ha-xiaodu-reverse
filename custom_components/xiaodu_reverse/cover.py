"""Cover platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi
from .appliance_types import ApplianceTypes
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

POSITION_KEYS = (
    "degree",           # 小度窗帘实际使用的开合度（0-100）
    "position",
    "curtainPosition",
    "percentControl",
    "openPercent",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu curtains / covers."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuCover] = []
    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if ApplianceTypes.is_cover(types):
            entities.append(XiaoDuCover(coordinator, api, appliance_id))
    if entities:
        async_add_entities(entities)


class XiaoDuCover(XiaoDuEntity, CoverEntity):
    """A XiaoDu curtain or cover."""

    _attr_has_entity_name = True
    _attr_device_class = CoverDeviceClass.CURTAIN

    # Ble-mesh curtains (botId=__ble_mesh__, proxied through a 小度智能屏) take
    # 15-30 s to travel and report back — measured up to 40 s for a 70% → 0%
    # round trip. Poll a few times so the UI catches up without a manual refresh.
    FOLLOW_UP_REFRESH_DELAYS: tuple[int, ...] = (10, 25, 45)

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, api, appliance_id, "cover")
        self._attr_name = None

    def _position_key(self) -> str | None:
        """Return the state key that carries the open percentage."""
        for key in POSITION_KEYS:
            if key in self.state_setting:
                return key
        return None

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return the supported cover features."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    @property
    def current_cover_position(self) -> int | None:
        """Return the current position (100 = fully open)."""
        key = self._position_key()
        if key is None:
            return None
        raw = self.attr_value(key)
        try:
            return max(0, min(100, int(float(raw))))
        except (TypeError, ValueError):
            return None

    @property
    def is_closed(self) -> bool | None:
        """Return True when the cover is fully closed."""
        position = self.current_cover_position
        if position is not None:
            return position == 0
        # Fall back to the on/off state: ON means opened.
        on = self.attr_value("turnOnState")
        if on is None:
            return None
        return str(on).strip().lower() not in ("on", "open", "opened")

    async def _async_follow_up_refresh(self, delay: float) -> None:
        """Refresh once after ``delay`` seconds (motor still moving)."""
        try:
            await asyncio.sleep(delay)
            if self.hass is None:
                return
            await self.coordinator.async_request_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("延迟刷新失败: %s", exc)

    def _schedule_follow_up_refresh(self) -> None:
        """Poll again while the motor finishes travelling."""
        for delay in self.FOLLOW_UP_REFRESH_DELAYS:
            self.hass.async_create_task(self._async_follow_up_refresh(delay))

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self.async_send(self._api.async_cover_open())
        self._schedule_follow_up_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self.async_send(self._api.async_cover_close())
        self._schedule_follow_up_refresh()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self.async_send(self._api.async_cover_stop())
        self._schedule_follow_up_refresh()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a given open percentage (0 = closed, 100 = open)."""
        try:
            position = int(kwargs.get(ATTR_POSITION, 0))
        except (TypeError, ValueError):
            return
        position = max(0, min(100, position))
        if position == 0:
            await self.async_send(self._api.async_cover_close())
        elif position == 100:
            await self.async_send(self._api.async_cover_open())
        else:
            await self.async_send(self._api.async_cover_set_position(position))
        self._schedule_follow_up_refresh()
