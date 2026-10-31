"""Climate platform for the XiaoDu Reverse integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import XiaoDuApi, XiaoDuApiError
from .appliance_types import ApplianceTypes
from .coordinator import XiaoDuCoordinator
from .entity import XiaoDuEntity, get_runtime, types_for

_LOGGER = logging.getLogger(__name__)

# XiaoDu mode code → HA HVAC mode
XIAODU_TO_HVAC: dict[str, HVACMode] = {
    "COOL": HVACMode.COOL,
    "HEAT": HVACMode.HEAT,
    "FAN": HVACMode.FAN_ONLY,
    "AUTO": HVACMode.AUTO,
    "DRY": HVACMode.DRY,
    "DEHUMIDIFICATION": HVACMode.DRY,
    "VENTILATION": HVACMode.FAN_ONLY,
}

HVAC_TO_XIAODU: dict[HVACMode, str] = {
    HVACMode.COOL: "COOL",
    HVACMode.HEAT: "HEAT",
    HVACMode.FAN_ONLY: "FAN",
    HVACMode.AUTO: "AUTO",
    HVACMode.DRY: "DEHUMIDIFICATION",
    HVACMode.OFF: "OFF",
}

DEFAULT_MIN_TEMP = 16.0
DEFAULT_MAX_TEMP = 32.0
DEFAULT_TARGET_STEP = 1.0


def _climate_features() -> ClimateEntityFeature:
    """Build the feature set tolerating enum changes across HA versions.

    Referencing a removed flag directly in the class body raises
    AttributeError at import time and takes the whole platform down, so each
    flag is looked up defensively.
    """
    features = ClimateEntityFeature(0)
    for name in ("TARGET_TEMPERATURE", "FAN_MODE", "TURN_ON", "TURN_OFF"):
        features |= getattr(ClimateEntityFeature, name, ClimateEntityFeature(0))
    return features


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XiaoDu air conditioners."""
    coordinator, apis = get_runtime(hass, entry)
    entities: list[XiaoDuClimate] = []
    for appliance_id, api in apis.items():
        types = types_for(coordinator, api, appliance_id)
        if ApplianceTypes.is_climate(types):
            entities.append(XiaoDuClimate(coordinator, api, appliance_id))
    if entities:
        async_add_entities(entities)


class XiaoDuClimate(XiaoDuEntity, ClimateEntity):
    """A XiaoDu air conditioner."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = DEFAULT_TARGET_STEP
    _enable_turn_on_off_backwards_compatibility = False
    _attr_supported_features = _climate_features()

    def __init__(
        self,
        coordinator: XiaoDuCoordinator,
        api: XiaoDuApi,
        appliance_id: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, api, appliance_id, "climate")
        self._attr_name = None

    # ── ranges ─────────────────────────────────────────────────────────

    def _temp_range(self) -> tuple[float, float]:
        """Return the device temperature range."""
        entry = self.state_setting.get("targetTemperature") or {}
        low = entry.get("min")
        high = entry.get("max")
        if low is None:
            range_map = entry.get("valueRangeMap") or {}
            low = range_map.get("min")
            high = range_map.get("max")
        try:
            lo = float(low) if low is not None else DEFAULT_MIN_TEMP
            hi = float(high) if high is not None else DEFAULT_MAX_TEMP
        except (TypeError, ValueError):
            lo, hi = DEFAULT_MIN_TEMP, DEFAULT_MAX_TEMP
        if lo >= hi:
            lo, hi = DEFAULT_MIN_TEMP, DEFAULT_MAX_TEMP
        return lo, hi

    @property
    def min_temp(self) -> float:
        """Return the minimum target temperature."""
        return self._temp_range()[0]

    @property
    def max_temp(self) -> float:
        """Return the maximum target temperature."""
        return self._temp_range()[1]

    # ── state ──────────────────────────────────────────────────────────

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the available HVAC modes."""
        entry = self.state_setting.get("mode") or {}
        codes = entry.get("valueRangeMap")
        modes: list[HVACMode] = [HVACMode.OFF]
        if isinstance(codes, dict) and codes:
            for code in codes:
                hvac = XIAODU_TO_HVAC.get(str(code).upper())
                if hvac and hvac not in modes:
                    modes.append(hvac)
        if len(modes) == 1:
            modes.extend(
                [HVACMode.COOL, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.AUTO]
            )
        return modes

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the available fan speeds."""
        entry = self.state_setting.get("fanSpeed") or {}
        codes = entry.get("valueRangeMap")
        if isinstance(codes, dict) and codes:
            return [str(v) for v in codes.values()]
        return None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        if not self._is_on:
            return HVACMode.OFF
        code = str(self.state("mode") or "").upper()
        return XIAODU_TO_HVAC.get(code, HVACMode.AUTO)

    @property
    def _is_on(self) -> bool:
        """Return True when the AC reports ON."""
        value = self.state("turnOnState")
        return str(value).strip().lower() == "on"

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current running action."""
        if not self._is_on:
            return HVACAction.OFF
        mode = self.hvac_mode
        if mode == HVACMode.COOL:
            return HVACAction.COOLING
        if mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if mode == HVACMode.DRY:
            return HVACAction.DRYING
        if mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Return the measured room temperature."""
        value = self.state("temperature") or self.state("currentTemperature")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        value = self.state("targetTemperature") or self.state("temperatureSet")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan speed label."""
        entry = self.state_setting.get("fanSpeed") or {}
        codes = entry.get("valueRangeMap") or {}
        current = self.state("fanSpeed")
        if isinstance(codes, dict):
            return str(codes.get(str(current), current)) if current is not None else None
        return str(current) if current is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose a few raw device fields for troubleshooting."""
        return {
            "appliance_id": self._appliance_id,
            "raw_mode": self.state("mode"),
            "raw_fan_speed": self.state("fanSpeed"),
        }

    # ── commands ───────────────────────────────────────────────────────

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        code = HVAC_TO_XIAODU.get(hvac_mode)
        if not code:
            raise ValueError(f"不支持的模式: {hvac_mode}")
        if not self._is_on:
            await self.async_send(self._api.async_ac_on(), refresh=False)
        await self.async_send(self._api.async_ac_set_mode(code))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            temperature = kwargs.get(ATTR_TARGET_TEMP_LOW)
        if temperature is None and kwargs.get(ATTR_TARGET_TEMP_HIGH) is None:
            return
        if temperature is None:
            temperature = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        target = self._clamp(float(temperature))

        # Fast path: direct target-temperature directive.
        try:
            ok, error = await self._api.async_ac_set_temperature(target)
        except XiaoDuApiError as exc:
            _LOGGER.debug("直接设置温度异常（%s），改用增减指令逼近", exc)
            ok, error = False, str(exc)
        if not ok:
            _LOGGER.debug("直接设置温度失败（%s），改用增减指令逼近", error)
            await self._step_towards(target)
        await self.coordinator.async_request_refresh()

    def _clamp(self, value: float) -> float:
        """Clamp a temperature to the device range."""
        low, high = self._temp_range()
        return max(low, min(high, value))

    async def _step_towards(self, target: float) -> None:
        """Nudge the setpoint with increment/decrement directives."""
        current = self.target_temperature
        if current is None:
            return
        delta = int(round(target - current))
        for _ in range(min(abs(delta), 15)):
            try:
                if delta > 0:
                    ok, err = await self._api.async_ac_increment_temperature()
                else:
                    ok, err = await self._api.async_ac_decrement_temperature()
            except XiaoDuApiError as exc:
                _LOGGER.debug("温度逼近失败: %s", exc)
                return
            if not ok:
                _LOGGER.debug("温度逼近被拒绝: %s", err)
                return

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed."""
        entry = self.state_setting.get("fanSpeed") or {}
        codes = entry.get("valueRangeMap") or {}
        code = fan_mode
        if isinstance(codes, dict):
            for key, label in codes.items():
                if str(label) == str(fan_mode):
                    code = str(key)
                    break
        await self.async_send(self._api.async_set_mode(code))

    async def async_turn_on(self) -> None:
        """Turn the AC on."""
        await self.async_send(self._api.async_ac_on())

    async def async_turn_off(self) -> None:
        """Turn the AC off."""
        await self.async_send(self._api.async_ac_off())
