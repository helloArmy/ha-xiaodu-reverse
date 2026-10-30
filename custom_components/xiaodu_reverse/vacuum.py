"""Vacuum platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi
from .appliance_types import ApplianceTypes
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

STATE_KEYS = ("cleaningState", "cleaningStatus", "workStatus", "robotStatus")

STATE_MAP: dict[str, VacuumActivity] = {
    "CLEANING": VacuumActivity.CLEANING,
    "CLEAN": VacuumActivity.CLEANING,
    "RUNNING": VacuumActivity.CLEANING,
    "PAUSED": VacuumActivity.PAUSED,
    "PAUSE": VacuumActivity.PAUSED,
    "STOPPED": VacuumActivity.IDLE,
    "IDLE": VacuumActivity.IDLE,
    "STANDBY": VacuumActivity.IDLE,
    "RETURNING": VacuumActivity.RETURNING,
    "GO_HOME": VacuumActivity.RETURNING,
    "CHARGING": VacuumActivity.DOCKED,
    "DOCKED": VacuumActivity.DOCKED,
    "FULL_CHARGED": VacuumActivity.DOCKED,
    "ERROR": VacuumActivity.ERROR,
    "FAULT": VacuumActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu robot vacuums."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuVacuum] = []
    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if ApplianceTypes.is_vacuum(types):
            entities.append(XiaoDuVacuum(coordinator, api, appliance_id))
    if entities:
        async_add_entities(entities)


class XiaoDuVacuum(XiaoDuEntity, StateVacuumEntity):
    """A XiaoDu robot vacuum."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.BATTERY
    )

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the vacuum."""
        super().__init__(coordinator, api, appliance_id, "vacuum")
        self._attr_name = None

    def _raw_state(self) -> str | None:
        """Return the raw cleaning state string."""
        for key in STATE_KEYS:
            value = self.state(key)
            if value is not None:
                return str(value).strip().upper()
        on_off = self.state("turnOnState")
        if on_off is not None:
            return "CLEANING" if str(on_off).strip().lower() == "on" else "DOCKED"
        return None

    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current vacuum activity."""
        raw = self._raw_state()
        if raw is None:
            return None
        return STATE_MAP.get(raw, VacuumActivity.IDLE)

    @property
    def battery_level(self) -> int | None:
        """Return the battery percentage."""
        value = self.state("battery")
        try:
            return max(0, min(100, int(float(value)))) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def async_start(self) -> None:
        """Start cleaning."""
        await self.async_send(self._api.async_vacuum_start())

    async def async_pause(self) -> None:
        """Pause cleaning."""
        await self.async_send(self._api.async_vacuum_pause())

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning."""
        await self.async_send(self._api.async_vacuum_stop())

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send the vacuum back to its dock."""
        await self.async_send(self._api.async_vacuum_dock())
