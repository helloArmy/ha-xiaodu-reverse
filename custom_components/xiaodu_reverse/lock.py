"""Lock platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi
from .appliance_types import ApplianceTypes
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

LOCK_KEYS = ("locked", "lockState", "doorLockState", "lockStatus", "lockStateValue")
LOCKED_VALUES = ("locked", "lock", "on", "1", "true", "已上锁", "上锁")
UNLOCKED_VALUES = ("unlocked", "unlock", "off", "0", "false", "已开锁", "开锁")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu door locks."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuLock] = []
    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if ApplianceTypes.is_lock(types):
            entities.append(XiaoDuLock(coordinator, api, appliance_id))
    if entities:
        async_add_entities(entities)


class XiaoDuLock(XiaoDuEntity, LockEntity):
    """A XiaoDu door lock."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the lock."""
        super().__init__(coordinator, api, appliance_id, "lock")
        self._attr_name = None

    @property
    def is_locked(self) -> bool | None:
        """Return True when the lock reports locked."""
        for key in LOCK_KEYS:
            value = self.state(key)
            if value is None:
                continue
            text = str(value).strip().lower()
            if text in LOCKED_VALUES:
                return True
            if text in UNLOCKED_VALUES:
                return False
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""
        await self.async_send(self._api.async_lock())

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door."""
        await self.async_send(self._api.async_unlock())
