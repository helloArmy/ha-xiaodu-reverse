"""Vacuum platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:
    # Home Assistant 2025.1 起用 VacuumActivity 枚举表示吸尘器状态
    from homeassistant.components.vacuum import VacuumActivity

    _HAS_ACTIVITY = True
except ImportError:  # HA 2024.x：只能用 state 字符串
    VacuumActivity = None  # type: ignore[assignment,misc]
    _HAS_ACTIVITY = False

from .api import XiaoDuApi
from .appliance_types import ApplianceTypes
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

STATE_KEYS = ("cleaningState", "cleaningStatus", "workStatus", "robotStatus")

# 原始状态串 → HA 标准状态名（字符串，兼容所有版本）
STATE_NAMES: dict[str, str] = {
    "CLEANING": "cleaning",
    "CLEAN": "cleaning",
    "RUNNING": "cleaning",
    "PAUSED": "paused",
    "PAUSE": "paused",
    "STOPPED": "idle",
    "IDLE": "idle",
    "STANDBY": "idle",
    "RETURNING": "returning",
    "GO_HOME": "returning",
    "CHARGING": "docked",
    "DOCKED": "docked",
    "FULL_CHARGED": "docked",
    "ERROR": "error",
    "FAULT": "error",
}


def _vacuum_features() -> VacuumEntityFeature:
    """Build the feature set tolerating enum changes across HA versions.

    ``VacuumEntityFeature.BATTERY`` was removed in HA 2026.10 (battery is now
    reported through the ``battery_level`` property and a separate sensor), so
    every flag is looked up defensively instead of being hard-referenced in the
    class body — an AttributeError there breaks the whole platform import.
    """
    features = VacuumEntityFeature(0)
    for name in ("START", "PAUSE", "STOP", "RETURN_HOME", "STATE", "BATTERY", "LOCATE"):
        features |= getattr(VacuumEntityFeature, name, VacuumEntityFeature(0))
    return features


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
    _attr_supported_features = _vacuum_features()

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
            value = self.attr_value(key)
            if value is not None:
                return str(value).strip().upper()
        on_off = self.attr_value("turnOnState")
        if on_off is not None:
            return "CLEANING" if str(on_off).strip().lower() == "on" else "DOCKED"
        return None

    @property
    def battery_level(self) -> int | None:
        """Return the battery percentage."""
        value = self.attr_value("battery")
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


# ── 跨版本状态上报 ─────────────────────────────────────────────────────
# HA 2025.1 起改用 activity 属性 + VacuumActivity 枚举，2024.x 只认 state 字符串。
# 两个属性的语义互斥（新版若覆盖 state 会破坏基类派生逻辑），所以按版本挂载其一。

if _HAS_ACTIVITY:

    def _get_activity(self: XiaoDuVacuum) -> VacuumActivity | None:
        """Return the current vacuum activity (HA 2025.1+)."""
        raw = self._raw_state()
        if raw is None:
            return None
        name = STATE_NAMES.get(raw, "idle").upper()
        return getattr(VacuumActivity, name, VacuumActivity.IDLE)

    XiaoDuVacuum.activity = property(_get_activity)  # type: ignore[assignment]

else:

    def _get_state(self: XiaoDuVacuum) -> str | None:
        """Return the current vacuum state string (HA 2024.x)."""
        raw = self._raw_state()
        if raw is None:
            return None
        return STATE_NAMES.get(raw, "idle")

    XiaoDuVacuum.state = property(_get_state)  # type: ignore[assignment]
