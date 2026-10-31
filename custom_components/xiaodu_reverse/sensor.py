"""Sensor platform for the XiaoDu Reverse integration.

Every numeric / textual attribute found in ``stateSetting`` that maps to a known
key becomes its own sensor entity — temperature, humidity, PM2.5, filter life,
work status and so on.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi
from .appliance_types import (
    SENSOR_ATTRIBUTES,
    STATUS_ATTRIBUTES,
    ApplianceTypes,
)
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)


def _device_class(name: str | None) -> SensorDeviceClass | None:
    """Resolve a device class by name, tolerating version differences."""
    if not name:
        return None
    return getattr(SensorDeviceClass, str(name).upper(), None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one sensor per supported stateSetting attribute."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuSensor] = []

    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        state_setting = coordinator.get_state_setting(appliance_id)
        if not state_setting:
            continue
        sensor_capable = ApplianceTypes.is_sensor_capable(types)

        for key, (label, unit, device_class_name) in SENSOR_ATTRIBUTES.items():
            if key not in state_setting:
                continue
            entities.append(
                XiaoDuSensor(
                    coordinator,
                    api,
                    appliance_id,
                    key=key,
                    label=label,
                    unit=unit,
                    device_class_name=device_class_name,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

        if sensor_capable:
            for key, label in STATUS_ATTRIBUTES.items():
                if key not in state_setting:
                    continue
                if key in SENSOR_ATTRIBUTES:
                    continue  # already covered as a measurement
                entities.append(
                    XiaoDuSensor(
                        coordinator,
                        api,
                        appliance_id,
                        key=key,
                        label=label,
                        unit=None,
                        device_class_name=None,
                        state_class=None,
                    )
                )

    if entities:
        async_add_entities(entities)


class XiaoDuSensor(XiaoDuEntity, SensorEntity):
    """One attribute of a XiaoDu appliance exposed as a sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
        *,
        key: str,
        label: str,
        unit: str | None,
        device_class_name: str | None,
        state_class: SensorStateClass | None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, api, appliance_id, f"sensor_{key}", label)
        self._key = key
        self._attr_native_unit_of_measurement = unit
        device_class = _device_class(device_class_name)
        if device_class is not None:
            self._attr_device_class = device_class
        if state_class is not None and device_class is not None:
            self._attr_state_class = state_class
        elif state_class is not None and unit is not None:
            self._attr_state_class = state_class

    @property
    def native_value(self) -> Any:
        """Return the current attribute value."""
        value = self.attr_value(self._key)
        if value is None:
            return None
        if isinstance(value, (int, float, bool)):
            return value
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if number.is_integer() else number
