"""Light platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .appliance_types import ApplianceTypes, state_is_on
from .coordinator import XiaoDuCoordinator
from .api import XiaoDuApi
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

DEFAULT_MIN_KELVIN = 2700
DEFAULT_MAX_KELVIN = 6500


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu lights."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuLight] = []
    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if ApplianceTypes.is_light(types):
            entities.append(XiaoDuLight(coordinator, api, appliance_id))
    if entities:
        async_add_entities(entities)


class XiaoDuLight(XiaoDuEntity, LightEntity):
    """A XiaoDu light (on/off, optional brightness / color temp / scenes)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator, api, appliance_id, "light")
        self._attr_name = None

    # ── capability detection (state-driven, like hass_xiaodu) ──────────

    def _supports(self, key: str) -> bool:
        """Return True when the device exposes the given state attribute."""
        return key in self.state_setting

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return the color modes the device actually supports."""
        has_brightness = self._supports("brightness")
        has_color_temp = self._supports("colorTemperatureInKelvin")
        if has_brightness and has_color_temp:
            return {ColorMode.COLOR_TEMP}
        if has_brightness:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self) -> ColorMode:
        """Return the currently active color mode."""
        modes = self.supported_color_modes
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_features(self) -> LightEntityFeature:
        """Return supported features (effects when a mode map exists)."""
        if self._effect_map():
            return LightEntityFeature(LightEntityFeature.EFFECT)
        return LightEntityFeature(0)

    # ── state ──────────────────────────────────────────────────────────

    @property
    def is_on(self) -> bool:
        """Return True when the light is on."""
        return state_is_on(self.state_setting)

    @property
    def brightness(self) -> int | None:
        """Return brightness as 0-255."""
        raw = self.state("brightness")
        if raw is None:
            return None
        try:
            return max(0, min(255, round(float(raw) / 100 * 255)))
        except (TypeError, ValueError):
            return None

    def _kelvin_range(self) -> tuple[int, int]:
        """Return the device's kelvin range (falls back to 2700-6500)."""
        entry = self.state_setting.get("colorTemperatureInKelvin") or {}
        kelvin_map = entry.get("valueKelvinRangeMap") or {}
        try:
            low = int(kelvin_map.get("min", DEFAULT_MIN_KELVIN))
            high = int(kelvin_map.get("max", DEFAULT_MAX_KELVIN))
        except (TypeError, ValueError):
            low, high = DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN
        if low >= high:
            low, high = DEFAULT_MIN_KELVIN, DEFAULT_MAX_KELVIN
        return low, high

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the minimum supported kelvin."""
        return self._kelvin_range()[0]

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the maximum supported kelvin."""
        return self._kelvin_range()[1]

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the current color temperature in kelvin."""
        raw = self.state("colorTemperatureInKelvin")
        if raw is None:
            return None
        low, high = self._kelvin_range()
        try:
            pct = max(0.0, min(100.0, float(raw)))
        except (TypeError, ValueError):
            return None
        return round(pct / 100 * (high - low)) + low

    def _effect_map(self) -> dict[str, str]:
        """Return ``{mode_code: label}`` for the light scenes."""
        entry = self.state_setting.get("mode") or {}
        mapping = entry.get("valueRangeMap")
        if isinstance(mapping, dict) and mapping:
            return {str(k): str(v) for k, v in mapping.items()}
        return {}

    @property
    def effect_list(self) -> list[str] | None:
        """Return the available scene names."""
        mapping = self._effect_map()
        return list(mapping.values()) if mapping else None

    @property
    def effect(self) -> str | None:
        """Return the currently selected scene name."""
        mapping = self._effect_map()
        if not mapping:
            return None
        current = self.state("mode")
        return mapping.get(str(current))

    # ── commands ───────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally setting brightness / kelvin / scene."""
        if kwargs.get(ATTR_BRIGHTNESS) is not None:
            percent = max(1, min(100, round(float(kwargs[ATTR_BRIGHTNESS]) / 255 * 100)))
            await self.async_send(self._api.async_set_brightness(percent), refresh=False)
        else:
            await self.async_send(self._api.async_turn_on(), refresh=False)

        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        if kelvin is None:
            kelvin = kwargs.get("color_temp")
        if kelvin is not None:
            low, high = self._kelvin_range()
            span = max(1, high - low)
            percent = round((float(kelvin) - low) / span * 100)
            await self.async_send(
                self._api.async_set_color_temp(max(0, min(100, percent))),
                refresh=False,
            )

        effect = kwargs.get(ATTR_EFFECT)
        if effect:
            mapping = self._effect_map()
            code = next((k for k, v in mapping.items() if v == effect), effect)
            await self.async_send(self._api.async_set_mode(code), refresh=False)

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.async_send(self._api.async_turn_off())
