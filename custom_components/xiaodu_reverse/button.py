"""Button platform for the XiaoDu Reverse integration.

Two kinds of buttons are exposed:

* **scene triggers** — a XiaoDu device whose type is ``SCENE_TRIGGER``; pressing
  it fires ``TurnOnRequest`` exactly like pressing it in the XiaoDu app;
* **panel actions** — one-shot entries from a device's ``上下 / 行程`` panel
  (e.g. clothes-rack raise / lower), sent with the panel's own header name.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi
from .appliance_types import ApplianceTypes, PanelFunction, iter_panel_buttons
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu buttons."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[ButtonEntity] = []

    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        appliance = coordinator.get_appliance(appliance_id)

        if ApplianceTypes.is_button(types):
            entities.append(XiaoDuSceneButton(coordinator, api, appliance_id))

        for function in iter_panel_buttons(appliance):
            entities.append(
                XiaoDuPanelButton(coordinator, api, appliance_id, function)
            )

    if entities:
        async_add_entities(entities)


class XiaoDuSceneButton(XiaoDuEntity, ButtonEntity):
    """A XiaoDu scene trigger."""

    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.IDENTIFY

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the scene button."""
        super().__init__(
            coordinator, api, appliance_id, "button_scene", name_suffix="执行场景"
        )
        self._attr_icon = "mdi:gesture-tap-button"

    async def async_press(self) -> None:
        """Trigger the scene."""
        await self.async_send(self._api.async_turn_on())


class XiaoDuPanelButton(XiaoDuEntity, ButtonEntity):
    """A one-shot action from a device panel (e.g. rack up / down)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
        function: PanelFunction,
    ) -> None:
        """Initialize the panel button."""
        super().__init__(
            coordinator,
            api,
            appliance_id,
            f"panel_button_{function.unique_suffix}",
            name_suffix=function.label,
        )
        self._function = function
        self._attr_icon = "mdi:gesture-tap"

    async def async_press(self) -> None:
        """Fire the panel action."""
        await self.async_send(
            self._api.async_panel_button(
                self._function.switch_type,
                self._function.header_name_on,
                self._function.payload,
            )
        )
