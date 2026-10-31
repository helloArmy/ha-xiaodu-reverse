"""Cover platform for the XiaoDu Reverse integration."""

from __future__ import annotations

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
        features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        if self._position_key():
            features |= CoverEntityFeature.SET_POSITION
        return features

    @property
    def current_cover_position(self) -> int | None:
        """Return the current position (100 = fully open)."""
        key = self._position_key()
        if key is None:
            return None
        raw = self.state(key)
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
        on = self.state("turnOnState")
        if on is None:
            return None
        return str(on).strip().lower() not in ("on", "open", "opened")

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self.async_send(self._api.async_cover_open())

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self.async_send(self._api.async_cover_close())

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self.async_send(self._api.async_cover_stop())

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a position.

        XiaoDu curtains are not uniformly position-capable, so this maps a
        request to fully-open / fully-closed for anything that is not already
        the current side of the range.
        """
        position = int(kwargs.get(ATTR_POSITION, 0))
        if position >= 50:
            await self.async_send(self._api.async_cover_open())
        else:
            await self.async_send(self._api.async_cover_close())
