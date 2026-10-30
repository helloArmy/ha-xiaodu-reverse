"""Switch platform for the XiaoDu Reverse integration.

Besides the main on/off switch, this mirrors hass_xiaodu's panel behaviour:
every entry of a device's ``功能控制`` panel becomes its own switch entity,
controlled through ``directivesend`` with the panel's own ``headerName`` and
payload fragment.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi
from .appliance_types import (
    ApplianceTypes,
    PanelFunction,
    extract_panel_functions,
    panel_is_on,
    state_is_on,
)
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu switches and panel function switches."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[SwitchEntity] = []

    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if not ApplianceTypes.is_switch(types):
            continue

        appliance = coordinator.get_appliance(appliance_id)
        entities.append(XiaoDuSwitch(coordinator, api, appliance_id))

        # Panel-driven function switches (clothes rack, air cooler, ...).
        for function in extract_panel_functions(appliance):
            if function.is_button:
                continue  # one-shot actions belong to the button platform
            entities.append(
                XiaoDuPanelSwitch(coordinator, api, appliance_id, function)
            )

    if entities:
        async_add_entities(entities)


class XiaoDuSwitch(XiaoDuEntity, SwitchEntity):
    """Main on/off switch of a XiaoDu appliance."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, api, appliance_id, "switch")
        self._attr_name = None

    @property
    def is_on(self) -> bool:
        """Return True when the appliance is on."""
        return state_is_on(self.state_setting)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the appliance on."""
        await self.async_send(self._api.async_turn_on())

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the appliance off."""
        await self.async_send(self._api.async_turn_off())


class XiaoDuPanelSwitch(XiaoDuEntity, SwitchEntity):
    """One function switch taken from a device's ``panels`` definition."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
        function: PanelFunction,
    ) -> None:
        """Initialize the panel switch."""
        super().__init__(
            coordinator,
            api,
            appliance_id,
            f"panel_{function.unique_suffix}",
            name_suffix=function.label,
        )
        self._function = function
        if function.icon and str(function.icon).startswith("mdi:"):
            self._attr_icon = str(function.icon)

    @property
    def is_on(self) -> bool:
        """Return True when the function reads back as its ON value."""
        result = panel_is_on(
            self.state_setting,
            self._function.switch_type,
            self._function.type_value,
        )
        return bool(result)

    @property
    def available(self) -> bool:
        """Return True when the device reports this function at all."""
        if not super().available:
            return False
        if not self._function.switch_type:
            return False
        # A function missing from stateSetting is still controllable on most
        # devices — keep the entity available but report it as off.
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the panel's ON directive."""
        await self.async_send(
            self._api.async_panel_switch_on(
                self._function.switch_type,
                self._function.type_value,
                self._function.header_name_on,
                self._function.header_name_off,
                self._function.payload,
            )
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the panel's OFF directive."""
        await self.async_send(
            self._api.async_panel_switch_off(
                self._function.switch_type,
                self._function.type_value,
                self._function.header_name_on,
                self._function.header_name_off,
                self._function.payload,
            )
        )
