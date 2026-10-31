"""Fan platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .api import XiaoDuApi
from .appliance_types import ApplianceTypes, state_is_on
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

SPEED_RANGE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu fans."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuFan] = []
    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if ApplianceTypes.is_fan(types):
            entities.append(XiaoDuFan(coordinator, api, appliance_id))
    if entities:
        async_add_entities(entities)


class XiaoDuFan(XiaoDuEntity, FanEntity):
    """A XiaoDu fan."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, api, appliance_id, "fan")
        self._attr_name = None

    @property
    def supported_features(self) -> FanEntityFeature:
        """Return the supported fan features."""
        features = FanEntityFeature(0)
        if "fanSpeed" in self.state_setting:
            features |= FanEntityFeature.SET_SPEED
        if "fanOscillation" in self.state_setting:
            features |= FanEntityFeature.OSCILLATE
        return features

    @property
    def is_on(self) -> bool:
        """Return True when the fan is on."""
        return state_is_on(self.state_setting)

    @property
    def percentage(self) -> int | None:
        """Return the fan speed as a percentage."""
        raw = self.attr_value("fanSpeed")
        if raw is None:
            return None
        try:
            return ranged_value_to_percentage(SPEED_RANGE, float(raw))
        except (TypeError, ValueError):
            return None

    @property
    def oscillating(self) -> bool | None:
        """Return True when oscillation is on."""
        value = self.attr_value("fanOscillation")
        if value is None:
            return None
        return str(value).strip().lower() == "on"

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally at a given speed."""
        if self.supported_features & FanEntityFeature.SET_SPEED and percentage:
            speed = round(percentage_to_ranged_value(SPEED_RANGE, percentage))
            await self.async_send(self._api.async_set_fan_speed(int(speed)), refresh=False)
        else:
            await self.async_send(self._api.async_turn_on(), refresh=False)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.async_send(self._api.async_turn_off())

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed."""
        if percentage == 0:
            await self.async_turn_off()
            return
        speed = round(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self.async_send(self._api.async_set_fan_speed(int(speed)))

    async def async_oscillate(self, oscillating: bool) -> None:
        """Turn oscillation on/off."""
        await self.async_send(self._api.async_set_fan_oscillation(oscillating))
