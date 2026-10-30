"""Appliance type classification and ``panels`` parsing for XiaoDu.

Type tables follow RockJesus/ha-xiaodu (``appliance_types.py``); the panel
extraction follows 2331892928/hass_xiaodu's ``switch.py`` / ``light.py``
behaviour, where each entry of the ``功能控制`` panel becomes its own entity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .const import PANEL_TITLE_FUNCTIONS

_LOGGER = logging.getLogger(__name__)

# ── Appliance type → HA platform mappings ───────────────────────────────
LIGHT_TYPES = {"LIGHT", "LAMP", "LIGHT_STRIP", "DESK_LAMP", "CEILING_LIGHT"}

SWITCH_TYPES = {
    "SOCKET",
    "SWITCH",
    "OUTLET",
    "PLUG",
    "WASHING_MACHINE",
    "HEATER",
    "AIR_FRESHER",
    "WINDOW_OPENER",
    "CLOTHES_RACK",
    "WATER_HEATER",
    "HUMIDIFIER",
    "DEHUMIDIFIER",
    "AIR_PURIFIER",
    "KETTLE",
    "RICE_COOKER",
    "MICROWAVE",
    "MICROWAVE_OVEN",
    "OVEN",
    "FRIDGE",
    "DISHWASHER",
    "GAS_STOVE",
    "RANGE_HOOD",
    "PRESSURE_COOKER",
    "INDUCTION_COOKER",
    "HIGH_SPEED_BLENDER",
    "PRINTER",
    "ROBOT",
    "WATER_PURIFIER",
    "SET_TOP_BOX",
    "TV_SET",
    "WEBCAM",
    "ELECTRIC_BLANKET",
    "MASSAGE_CHAIR",
    "COFFEE_MACHINE",
    "WINE_CELLAR",
    "WATER_DISPENSER",
    "TOILET",
    "SOY_MILK_MAKER",
    "BOTTLE_WARMER",
    "DRYER",
    "WALL_HUNG_BOILER",
    "MOSQUITO_DISPELLER",
    "TREADMILL",
    "FAN_HEATER",
    "AQUARIUM",
    "PET_FEEDER",
    "CURTAIN_SWITCH",
}

COVER_TYPES = {"CURTAIN", "WINDOW", "SHADE", "BLIND", "GARAGE_DOOR", "AWNING"}

CLIMATE_TYPES = {"AIR_CONDITION", "AIRCONDITION", "AC", "AIR_CONDITIONER"}

FAN_TYPES = {
    "FAN",
    "CEILING_FAN",
    "AIR_CONDITIONER_FAN",
    "VENTILATOR",
    "EXHAUST_FAN",
    "TOWER_FAN",
}

LOCK_TYPES = {"DOOR_LOCK", "SMART_LOCK", "FINGERPRINT_LOCK", "SMART_GATING"}

VACUUM_TYPES = {"SWEEPING_ROBOT", "ROBOT_VACUUM", "SWEEPER", "VACUUM_CLEANER"}

BUTTON_TYPES = {"SCENE_TRIGGER", "SCENE", "GENERAL_SCENE"}

SENSOR_CAPABLE_TYPES = {
    "AIR_PURIFIER",
    "HUMIDIFIER",
    "DEHUMIDIFIER",
    "HEATER",
    "AIR_CONDITION",
    "AIRCONDITION",
    "FAN",
    "WATER_HEATER",
    "AIR_MONITOR",
    "SENSOR",
    "WATER_PURIFIER",
    "TV_SET",
    "FRIDGE",
    "WASHING_MACHINE",
    "CLOTHES_RACK",
    "TEMP_HUMIDITY_SENSOR",
}

# ── Sensor attribute mappings ────────────────────────────────────────────
# stateSetting key → (name suffix, unit, device_class)
SENSOR_ATTRIBUTES: dict[str, tuple[str, str | None, str | None]] = {
    "temperature": ("温度", "°C", "temperature"),
    "humidity": ("湿度", "%", "humidity"),
    "pm25": ("PM2.5", "μg/m³", "pm25"),
    "pm2_5": ("PM2.5", "μg/m³", "pm25"),
    "pm10": ("PM10", "μg/m³", "pm10"),
    "pm1_0": ("PM1.0", "μg/m³", "pm1"),
    "co2": ("CO₂", "ppm", "carbon_dioxide"),
    "co": ("CO", "ppm", "carbon_monoxide"),
    "tvoc": ("TVOC", "mg/m³", "volatile_organic_compounds"),
    "voc": ("VOC", "mg/m³", "volatile_organic_compounds"),
    "formaldehyde": ("甲醛", "mg/m³", "formaldehyde"),
    "formaldehydeValue": ("甲醛", "mg/m³", "formaldehyde"),
    "airQuality": ("空气质量", None, "aqi"),
    "air_quality": ("空气质量", None, "aqi"),
    "aqi": ("AQI", None, "aqi"),
    "airQualityIndex": ("空气质量指数", None, "aqi"),
    "waterTemperature": ("水温", "°C", "temperature"),
    "water_level": ("水位", "%", None),
    "battery": ("电量", "%", "battery"),
    "illumination": ("照度", "lx", "illuminance"),
    "lightLevel": ("照度", "lx", "illuminance"),
    "noise": ("噪音", "dB", None),
    "noiseLevel": ("噪音", "dB", None),
    "tds": ("TDS", "ppm", None),
    "totalDissolvedSolids": ("TDS", "ppm", None),
    "filterLife": ("滤芯寿命", "%", None),
    "power": ("功率", "W", "power"),
    "energy": ("电量", "kWh", "energy"),
    "voltage": ("电压", "V", "voltage"),
    "current": ("电流", "A", "current"),
    "weight": ("重量", "kg", "weight"),
}

# Attributes that are status text, not measurements — exposed as plain sensors.
STATUS_ATTRIBUTES: dict[str, str] = {
    "workStatus": "工作状态",
    "waterPurifyStatus": "净水状态",
    "mode": "模式",
    "windSpeed": "风速",
    "fanSpeed": "风速",
    "cleaningState": "清扫状态",
    "program": "当前程序",
    "remainingTime": "剩余时间",
}

ALL_KNOWN_TYPES: frozenset[str] = frozenset(
    LIGHT_TYPES
    | SWITCH_TYPES
    | COVER_TYPES
    | CLIMATE_TYPES
    | FAN_TYPES
    | LOCK_TYPES
    | VACUUM_TYPES
    | BUTTON_TYPES
    | SENSOR_CAPABLE_TYPES
)


class ApplianceTypes:
    """Classify a XiaoDu appliance by its ``applianceTypes`` list."""

    @staticmethod
    def _contains(appliance_types: list[str], candidates: set[str]) -> bool:
        """Return True when any type is in ``candidates``."""
        upper = {str(t).upper() for t in appliance_types or []}
        return bool(upper & candidates)

    @classmethod
    def is_light(cls, appliance_types: list[str]) -> bool:
        """Return True for light devices."""
        return cls._contains(appliance_types, LIGHT_TYPES)

    @classmethod
    def is_switch(cls, appliance_types: list[str]) -> bool:
        """Return True for switch / outlet / generic on-off devices."""
        return cls._contains(appliance_types, SWITCH_TYPES)

    @classmethod
    def is_cover(cls, appliance_types: list[str]) -> bool:
        """Return True for curtain / cover devices."""
        return cls._contains(appliance_types, COVER_TYPES)

    @classmethod
    def is_climate(cls, appliance_types: list[str]) -> bool:
        """Return True for air conditioners."""
        return cls._contains(appliance_types, CLIMATE_TYPES)

    @classmethod
    def is_fan(cls, appliance_types: list[str]) -> bool:
        """Return True for fans."""
        return cls._contains(appliance_types, FAN_TYPES)

    @classmethod
    def is_lock(cls, appliance_types: list[str]) -> bool:
        """Return True for door locks."""
        return cls._contains(appliance_types, LOCK_TYPES)

    @classmethod
    def is_vacuum(cls, appliance_types: list[str]) -> bool:
        """Return True for robot vacuums."""
        return cls._contains(appliance_types, VACUUM_TYPES)

    @classmethod
    def is_button(cls, appliance_types: list[str]) -> bool:
        """Return True for scene triggers."""
        return cls._contains(appliance_types, BUTTON_TYPES)

    @classmethod
    def is_sensor_capable(cls, appliance_types: list[str]) -> bool:
        """Return True when the device typically reports measurements."""
        return cls._contains(appliance_types, SENSOR_CAPABLE_TYPES)

    @staticmethod
    def is_clothes_rack(appliance_types: list[str]) -> bool:
        """Return True for a clothes rack (multi-function panel device)."""
        return any(str(t).upper() == "CLOTHES_RACK" for t in appliance_types or [])

    @staticmethod
    def primary_type(appliance_types: list[str]) -> str:
        """Return the most specific type name for display purposes."""
        upper = [str(t).upper() for t in appliance_types or []]
        for group in (
            LIGHT_TYPES,
            CLIMATE_TYPES,
            COVER_TYPES,
            FAN_TYPES,
            LOCK_TYPES,
            VACUUM_TYPES,
            BUTTON_TYPES,
            SWITCH_TYPES,
        ):
            for t in upper:
                if t in group:
                    return t
        return upper[0] if upper else "UNKNOWN"


# ── state helpers ────────────────────────────────────────────────────────


def state_value(state_setting: dict[str, Any], key: str) -> Any:
    """Return ``stateSetting[key].value`` or None."""
    entry = (state_setting or {}).get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def state_is_on(state_setting: dict[str, Any], key: str = "turnOnState") -> bool:
    """Return True when the given on/off attribute reads ON."""
    value = state_value(state_setting, key)
    return str(value).strip().lower() in ("on", "1", "true", "open", "opened")


def panel_is_on(
    state_setting: dict[str, Any], switch_type: str, type_value: Any
) -> bool | None:
    """Return the on/off state of a panel function switch.

    A panel switch is "on" when ``stateSetting[switch_type].value`` equals the
    panel's own ``type_value`` — the semantics hass_xiaodu relies on.
    """
    if not switch_type or switch_type not in (state_setting or {}):
        return None
    value = state_value(state_setting, switch_type)
    if value is None:
        return None
    return str(value) == str(type_value)


# ── panels parsing ───────────────────────────────────────────────────────


@dataclass(slots=True)
class PanelFunction:
    """One function switch extracted from a device's ``panels`` definition."""

    switch_type: str
    type_value: Any
    label: str
    header_name_on: str = ""
    header_name_off: str = ""
    payload: str = ""
    icon: str | None = None
    is_button: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def unique_suffix(self) -> str:
        """Return a stable suffix for the entity unique id."""
        raw = f"{self.switch_type}_{self.type_value}"
        return "".join(ch if ch.isalnum() else "_" for ch in raw)


def _iter_panels(appliance: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the panel dicts that may hold function lists."""
    panels = appliance.get("panels")
    if not isinstance(panels, list):
        return []
    out: list[dict[str, Any]] = []
    for panel in panels:
        if not isinstance(panel, dict):
            continue
        title = str(panel.get("title") or "")
        if not title:
            out.append(panel)
            continue
        if any(key in title for key in PANEL_TITLE_FUNCTIONS):
            out.append(panel)
    return out


def extract_panel_functions(appliance: dict[str, Any]) -> list[PanelFunction]:
    """Extract function switches from an appliance's ``panels``.

    Mirrors hass_xiaodu: for each panel item, ``name`` is the switchType,
    ``value`` the typeValue, ``label`` the display name, and ``actions[0]`` /
    ``actions[1]`` carry the ON / OFF ``headerName``; the first action's
    ``payload`` (if any) is passed through verbatim.
    """
    functions: list[PanelFunction] = []
    seen: set[str] = set()

    for panel in _iter_panels(appliance):
        items = panel.get("list")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            switch_type = str(item.get("name") or "").strip()
            type_value = item.get("value")
            if not switch_type or type_value is None:
                continue
            label = str(item.get("label") or item.get("name") or switch_type)
            actions = item.get("actions") or []

            header_on = ""
            header_off = ""
            payload = ""
            for idx, action in enumerate(actions):
                if not isinstance(action, dict):
                    continue
                if idx == 0:
                    header_on = str(action.get("headerName") or "")
                elif idx == 1:
                    header_off = str(action.get("headerName") or "")
                if not payload and action.get("payload"):
                    raw = action["payload"]
                    payload = raw if isinstance(raw, str) else str(raw).replace("'", '"')

            if not header_on and not header_off:
                # A panel entry without actions is a pure button (e.g. up/down).
                header_on = ""

            key = f"{switch_type}|{type_value}"
            if key in seen:
                continue
            seen.add(key)

            functions.append(
                PanelFunction(
                    switch_type=switch_type,
                    type_value=type_value,
                    label=label,
                    header_name_on=header_on,
                    header_name_off=header_off,
                    payload=payload,
                    icon=item.get("icon"),
                    is_button=not header_on and not header_off,
                )
            )

    return functions


def iter_panel_buttons(appliance: dict[str, Any]) -> list[PanelFunction]:
    """Return panel entries that behave as one-shot buttons (no on/off pair)."""
    out: list[PanelFunction] = []
    panels = appliance.get("panels")
    if not isinstance(panels, list):
        return out
    for panel in panels:
        if not isinstance(panel, dict):
            continue
        title = str(panel.get("title") or "")
        if any(key in title for key in ("上下", "行程", "方向")):
            for item in panel.get("list") or []:
                if not isinstance(item, dict):
                    continue
                switch_type = str(item.get("name") or "").strip()
                if not switch_type:
                    continue
                header = ""
                payload = ""
                for action in item.get("actions") or []:
                    if not isinstance(action, dict):
                        continue
                    header = header or str(action.get("headerName") or "")
                    if not payload and action.get("payload"):
                        raw = action["payload"]
                        payload = raw if isinstance(raw, str) else str(raw).replace("'", '"')
                if not header:
                    continue
                out.append(
                    PanelFunction(
                        switch_type=switch_type,
                        type_value=item.get("value"),
                        label=str(item.get("label") or switch_type),
                        header_name_on=header,
                        payload=payload,
                        is_button=True,
                    )
                )
    return out
