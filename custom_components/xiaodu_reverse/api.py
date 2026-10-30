"""XiaoDu (小度) cloud smart-home API client.

Control layer follows **2331892928/hass_xiaodu** (``api/XiaoDuAPI.py``):

* device listing via ``POST /saiya/smarthome/appliance``
  (``GET_USER_ALL_APPLIANCES``) with the ``HOUSE_ID`` cookie;
* state via ``GET /saiya/smarthome/appliancedetails``
  (``stateSetting.<attr>.value``);
* control via ``GET /saiya/smarthome/directivesend?from=h5_control`` carrying a
  ``DuerOS.ConnectedHome.Control`` directive;
* **panel control** (``switch_panel_on`` / ``switch_panel_off`` /
  ``button_panel``) where a per-function ``panels`` entry supplies
  ``switchType`` / ``typeValue`` / ``headerNameOn`` / ``headerNameOff`` and an
  optional raw ``payload`` fragment that must be passed through verbatim.

Protocol details verified identical against RockJesus/ha-xiaodu's ``api.py``;
the only additions here are the direct set-temperature / speed directives that
ha-xiaodu exposes, used as an opt-in fast path.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .const import (
    API_APPLIANCE,
    API_APPLIANCE_DETAILS,
    API_DIRECTIVE_SEND,
    API_GATEWAY,
    API_MULTIHOUSE,
    DUEROS_NAMESPACE,
    HOUSE_ID_COOKIE,
    XIAODU_HOST,
)

_LOGGER = logging.getLogger(__name__)

# Directive names (DuerOS.ConnectedHome.Control)
CMD_TURN_ON = "TurnOnRequest"
CMD_TURN_OFF = "TurnOffRequest"
CMD_PAUSE = "PauseRequest"
CMD_SET_MODE = "SetModeRequest"
CMD_SET_BRIGHTNESS = "SetBrightnessPercentageRequest"
CMD_SET_COLOR_TEMP = "SetColorTemperatureRequest"
CMD_SET_TARGET_TEMP = "SetTargetTemperatureRequest"
CMD_INC_TEMP = "IncrementTemperatureRequest"
CMD_DEC_TEMP = "DecrementTemperatureRequest"
CMD_SET_FAN_SPEED = "SetFanSpeedPercentageRequest"
CMD_INC_FAN = "IncrementFanSpeedRequest"
CMD_DEC_FAN = "DecrementFanSpeedRequest"
CMD_SET_FAN_OSC = "SetFanOscillationRequest"
CMD_START_CLEAN = "StartCleaningRequest"
CMD_STOP_CLEAN = "StopCleaningRequest"
CMD_PAUSE_CLEAN = "PauseCleaningRequest"
CMD_RETURN_BASE = "ReturnToBaseRequest"
CMD_LOCK = "LockRequest"
CMD_UNLOCK = "UnlockRequest"

_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 "
    "Mobile/15E148 Safari/604.1"
)

_AUTH_FAILED_MARKERS = ("not login", "please login", "login expired", "未登录")


class XiaoDuApiError(Exception):
    """Base error for the XiaoDu cloud API."""


class LoginExpired(XiaoDuApiError):
    """Raised when the stored BDUSS cookie is no longer accepted."""


class CannotConnect(XiaoDuApiError):
    """Raised on transport-level failure."""


class XiaoDuApi:
    """API client for one XiaoDu account (optionally bound to one appliance)."""

    def __init__(
        self,
        cookie: str,
        session: aiohttp.ClientSession,
        house_id: str = "",
        appliance_id: str = "",
    ) -> None:
        """Initialize the client."""
        self.cookie = cookie
        self.session = session
        self.house_id = house_id
        self.appliance_id = appliance_id
        self.appliance_types: list[str] = []
        self._headers = self._build_headers()

    # ── plumbing ────────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        """Build the common request headers."""
        return {
            "Cookie": f"BDUSS={self.cookie};BDUSS_BFESS={self.cookie}",
            "User-Agent": _USER_AGENT,
            "content-type": "application/json",
            "device-id": "deviceid",
            "host": "xiaodu.baidu.com",
        }

    def update_cookie(self, cookie: str) -> None:
        """Replace the BDUSS cookie after a re-login."""
        self.cookie = cookie
        self._headers = self._build_headers()

    def _house_cookies(self, house_id: str | None = None) -> dict[str, str]:
        """Return the HOUSE_ID cookie for the given (or default) house."""
        hid = house_id if house_id is not None else self.house_id
        return {HOUSE_ID_COOKIE: hid or ""}

    @staticmethod
    def _is_auth_failure(msg: Any) -> bool:
        """Return True when the cloud says the session is gone."""
        text = str(msg or "").strip().lower()
        return any(marker in text for marker in _AUTH_FAILED_MARKERS)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        house_id: str | None = None,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """Perform a request and return the decoded JSON body.

        GET requests carry ``payload`` as a JSON body — the XiaoDu h5 client
        does the same, and the endpoint relies on it.
        """
        kwargs: dict[str, Any] = {
            "headers": self._headers,
            "cookies": self._house_cookies(house_id),
            "timeout": aiohttp.ClientTimeout(total=timeout),
        }
        if payload is not None:
            kwargs["json"] = payload
        if params is not None:
            kwargs["params"] = params
        try:
            async with self.session.request(
                method, f"{XIAODU_HOST}{path}", **kwargs
            ) as resp:
                text = await resp.text()
        except aiohttp.ClientError as exc:
            raise CannotConnect(f"请求小度失败: {exc}") from exc
        except TimeoutError as exc:
            raise CannotConnect("请求小度超时") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CannotConnect(f"小度返回非 JSON: {text[:120]}") from exc
        if not isinstance(data, dict):
            raise CannotConnect("小度返回结构异常")
        return data

    # ── authentication ─────────────────────────────────────────────────

    async def async_check_session(self) -> tuple[bool, str | None]:
        """Check whether the BDUSS cookie is still valid.

        Returns ``(is_valid, error)`` where error is ``invalid_auth`` /
        ``cannot_connect`` / ``unknown``.
        """
        payload = {"url": "dueros://smarthome.bot.dueros.ai/gateway/myspeaker"}
        try:
            data = await self._request_json("POST", API_GATEWAY, payload=payload)
        except CannotConnect:
            return False, "cannot_connect"
        if data.get("status") == 0:
            return True, None
        if self._is_auth_failure(data.get("msg")):
            return False, "invalid_auth"
        return False, "invalid_auth"

    # ── houses ─────────────────────────────────────────────────────────

    async def async_get_house_list(self) -> dict[str, str]:
        """Return ``{house_id: house_name}`` for the account."""
        data = await self._request_json(
            "POST", API_MULTIHOUSE, payload={"method": "HOUSE_LIST"}
        )
        if data.get("status") not in (0, None):
            _LOGGER.debug("房屋列表返回异常: %s", data.get("msg"))
        houses = (data.get("data") or {}).get("houseList") or []
        return {
            str(h.get("houseId")): str(h.get("houseName") or h.get("houseId"))
            for h in houses
            if h.get("houseId")
        }

    # ── appliances ─────────────────────────────────────────────────────

    async def async_get_all_appliances(
        self, house_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List every appliance in a house (GET_USER_ALL_APPLIANCES)."""
        payload = {
            "method": "GET_USER_ALL_APPLIANCES",
            "params": {"from": "h5_control", "withscene": 1, "generalscene": 3},
        }
        data = await self._request_json(
            "POST", API_APPLIANCE, house_id=house_id, payload=payload
        )
        if self._is_auth_failure(data.get("msg")):
            raise LoginExpired("小度登录状态已失效")
        return (data.get("data") or {}).get("appliances") or []

    async def async_get_device_dict(
        self, house_id: str | None = None
    ) -> dict[str, str]:
        """Return ``{appliance_id: friendly_name}`` for a house."""
        appliances = await self.async_get_all_appliances(house_id)
        return {
            str(a["applianceId"]): str(
                a.get("friendlyName") or a.get("applianceId")
            )
            for a in appliances
            if a.get("applianceId")
        }

    async def async_get_appliances_by_ids(
        self, house_id: str, appliance_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch metadata for a set of appliances (GET_APPLIANCES_BY_ID)."""
        if not appliance_ids:
            return []
        payload = {
            "enableCancelToken": True,
            "method": "GET_APPLIANCES_BY_ID",
            "params": {
                "from": "h5_control",
                "applianceIdList": list(appliance_ids),
                "clientCuidList": [],
                "enablecache": True,
            },
        }
        data = await self._request_json(
            "GET", API_APPLIANCE, house_id=house_id, payload=payload
        )
        if self._is_auth_failure(data.get("msg")):
            raise LoginExpired("小度登录状态已失效")
        return (data.get("data") or {}).get("appliances") or []

    async def async_get_detail(
        self, appliance_id: str | None = None
    ) -> dict[str, Any]:
        """Return ``data`` of ``appliancedetails`` for one appliance.

        The dict contains ``appliance`` → ``friendlyName`` / ``botName`` /
        ``groupName`` / ``applianceTypes`` / ``stateSetting`` / ``panels``.
        Raises :class:`LoginExpired` when the cookie is rejected.
        """
        aid = appliance_id or self.appliance_id
        if not aid:
            return {}
        payload = {"applianceId": aid, "version": 2, "from": "h5"}
        data = await self._request_json(
            "GET", API_APPLIANCE_DETAILS, payload=payload
        )
        if data.get("status") == 0:
            return data.get("data") or {}
        if self._is_auth_failure(data.get("msg")):
            raise LoginExpired(f"设备 {aid} 登录状态已失效")
        _LOGGER.debug("获取设备 %s 详情失败: %s", aid, data.get("msg"))
        return {}

    # ── directive plumbing ─────────────────────────────────────────────

    async def async_send_command(
        self, submit: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """Send one directive. Returns ``(success, error_message)``."""
        try:
            data = await self._request_json(
                "GET",
                API_DIRECTIVE_SEND,
                payload=submit,
                params={"from": "h5_control"},
            )
        except LoginExpired:
            raise
        except CannotConnect as exc:
            _LOGGER.warning("下发指令失败: %s", exc)
            return False, "cannot_connect"
        if data.get("status") == 0:
            return True, None
        msg = data.get("msg")
        if self._is_auth_failure(msg):
            raise LoginExpired("小度登录状态已失效，请重新登录")
        _LOGGER.debug("指令被拒绝 (%s): %s", submit.get("header", {}).get("name"), msg)
        return False, str(msg or "unknown_error")

    def _build_payload(
        self,
        name: str,
        *,
        parameters: dict[str, Any] | None = None,
        extra_state: dict[str, Any] | None = None,
        payload_version: int = 3,
        appliance_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a DuerOS control directive."""
        aid = appliance_id or self.appliance_id
        body: dict[str, Any] = {
            "applianceId": aid,
            "parameters": {"proxyConnectStatus": False, **(parameters or {})},
            "appliance": {"applianceId": [aid]},
        }
        if extra_state:
            body.update(extra_state)
        return {
            "header": {
                "namespace": DUEROS_NAMESPACE,
                "name": name,
                "payloadVersion": payload_version,
            },
            "payload": body,
        }

    # ── on/off ─────────────────────────────────────────────────────────

    async def async_turn_on(self) -> tuple[bool, str | None]:
        """Turn the appliance on."""
        return await self.async_send_command(
            self._build_payload(
                CMD_TURN_ON,
                parameters={"attribute": "turnOnState", "attributeValue": "ON"},
                extra_state={"turnOnState": {"value": "ON"}},
            )
        )

    async def async_turn_off(self) -> tuple[bool, str | None]:
        """Turn the appliance off."""
        return await self.async_send_command(
            self._build_payload(
                CMD_TURN_OFF,
                parameters={"attribute": "turnOnState", "attributeValue": "OFF"},
                extra_state={"turnOnState": {"value": "OFF"}},
            )
        )

    # ── light ──────────────────────────────────────────────────────────

    async def async_set_brightness(self, percentage: int) -> tuple[bool, str | None]:
        """Set brightness (0-100)."""
        pct = max(1, min(100, int(percentage)))
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_BRIGHTNESS,
                parameters={"attribute": "brightness", "attributeValue": pct},
                extra_state={"brightness": {"value": pct}},
            )
        )

    async def async_set_color_temp(self, percentage: int) -> tuple[bool, str | None]:
        """Set color temperature as a 0-100 percentage of the device range."""
        pct = max(0, min(100, int(percentage)))
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_COLOR_TEMP,
                parameters={
                    "attribute": "colorTemperatureInKelvin",
                    "attributeValue": pct,
                },
                extra_state={"colorTemperatureInKelvin": pct},
            )
        )

    async def async_set_mode(self, mode: str) -> tuple[bool, str | None]:
        """Set a mode (light scene / AC mode / fan mode)."""
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_MODE,
                parameters={"attribute": "mode", "attributeValue": mode},
                extra_state={"mode": {"value": mode}},
            )
        )

    # ── cover ──────────────────────────────────────────────────────────

    async def async_cover_open(self) -> tuple[bool, str | None]:
        """Open the curtain."""
        return await self.async_send_command(self._build_payload(CMD_TURN_ON))

    async def async_cover_close(self) -> tuple[bool, str | None]:
        """Close the curtain."""
        return await self.async_send_command(self._build_payload(CMD_TURN_OFF))

    async def async_cover_stop(self) -> tuple[bool, str | None]:
        """Stop the curtain."""
        return await self.async_send_command(self._build_payload(CMD_PAUSE))

    # ── climate ────────────────────────────────────────────────────────

    async def async_ac_on(self) -> tuple[bool, str | None]:
        """Turn the AC on (payloadVersion 1)."""
        return await self.async_send_command(
            self._build_payload(CMD_TURN_ON, payload_version=1)
        )

    async def async_ac_off(self) -> tuple[bool, str | None]:
        """Turn the AC off (payloadVersion 1)."""
        return await self.async_send_command(
            self._build_payload(CMD_TURN_OFF, payload_version=1)
        )

    async def async_ac_set_mode(self, mode: str) -> tuple[bool, str | None]:
        """Set AC mode: COOL / HEAT / FAN / AUTO / DEHUMIDIFICATION."""
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_MODE,
                extra_state={"mode": {"value": mode.upper()}},
                payload_version=1,
            )
        )

    async def async_ac_set_temperature(
        self, temperature: float
    ) -> tuple[bool, str | None]:
        """Set the AC target temperature directly."""
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_TARGET_TEMP,
                extra_state={"targetTemperature": {"value": round(temperature)}},
                payload_version=1,
            )
        )

    async def async_ac_increment_temperature(self) -> tuple[bool, str | None]:
        """Raise the AC target temperature by one degree."""
        return await self.async_send_command(
            self._build_payload(CMD_INC_TEMP, payload_version=1)
        )

    async def async_ac_decrement_temperature(self) -> tuple[bool, str | None]:
        """Lower the AC target temperature by one degree."""
        return await self.async_send_command(
            self._build_payload(CMD_DEC_TEMP, payload_version=1)
        )

    async def async_ac_increment_fan(self) -> tuple[bool, str | None]:
        """Raise the AC fan speed."""
        return await self.async_send_command(
            self._build_payload(CMD_INC_FAN, payload_version=1)
        )

    async def async_ac_decrement_fan(self) -> tuple[bool, str | None]:
        """Lower the AC fan speed."""
        return await self.async_send_command(
            self._build_payload(CMD_DEC_FAN, payload_version=1)
        )

    # ── fan ────────────────────────────────────────────────────────────

    async def async_set_fan_speed(self, percentage: int) -> tuple[bool, str | None]:
        """Set fan speed percentage (1-100)."""
        pct = max(1, min(100, int(percentage)))
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_FAN_SPEED,
                parameters={"attribute": "fanSpeed", "attributeValue": pct},
                extra_state={"fanSpeed": {"value": pct}},
            )
        )

    async def async_set_fan_oscillation(
        self, on: bool
    ) -> tuple[bool, str | None]:
        """Turn fan oscillation on/off."""
        value = "ON" if on else "OFF"
        return await self.async_send_command(
            self._build_payload(
                CMD_SET_FAN_OSC,
                parameters={"attribute": "fanOscillation", "attributeValue": value},
                extra_state={"fanOscillation": {"value": value}},
            )
        )

    # ── vacuum ─────────────────────────────────────────────────────────

    async def async_vacuum_start(self) -> tuple[bool, str | None]:
        """Start cleaning."""
        return await self.async_send_command(
            self._build_payload(
                CMD_START_CLEAN,
                extra_state={"cleaningState": {"value": "CLEANING"}},
            )
        )

    async def async_vacuum_stop(self) -> tuple[bool, str | None]:
        """Stop cleaning."""
        return await self.async_send_command(
            self._build_payload(
                CMD_STOP_CLEAN,
                extra_state={"cleaningState": {"value": "STOPPED"}},
            )
        )

    async def async_vacuum_pause(self) -> tuple[bool, str | None]:
        """Pause cleaning."""
        return await self.async_send_command(
            self._build_payload(
                CMD_PAUSE_CLEAN,
                extra_state={"cleaningState": {"value": "PAUSED"}},
            )
        )

    async def async_vacuum_dock(self) -> tuple[bool, str | None]:
        """Send the vacuum back to its dock."""
        return await self.async_send_command(
            self._build_payload(
                CMD_RETURN_BASE,
                extra_state={"cleaningState": {"value": "RETURNING"}},
            )
        )

    # ── lock ───────────────────────────────────────────────────────────

    async def async_lock(self) -> tuple[bool, str | None]:
        """Lock the door."""
        return await self.async_send_command(self._build_payload(CMD_LOCK))

    async def async_unlock(self) -> tuple[bool, str | None]:
        """Unlock the door."""
        return await self.async_send_command(self._build_payload(CMD_UNLOCK))

    # ── panel control (hass_xiaodu semantics) ──────────────────────────

    def _build_panel_payload(
        self,
        switch_type: str,
        type_value: Any,
        payload_fragment: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the payload body for a panel-driven function switch.

        ``payload_fragment`` is the raw ``actions[].payload`` object from the
        appliance ``panels`` definition. Some vendors reject the directive
        unless that fragment is merged into the payload verbatim, which is why
        hass_xiaodu passes it through — that behaviour is kept here.
        """
        aid = self.appliance_id
        body: dict[str, Any] = {
            "applianceId": aid,
            "parameters": {
                "attribute": switch_type,
                "attributeValue": type_value,
                "proxyConnectStatus": False,
            },
            "appliance": {"applianceId": [aid]},
            switch_type: {"value": type_value},
        }
        fragment = self._coerce_fragment(payload_fragment)
        if fragment:
            body.update(fragment)
        return body

    @staticmethod
    def _coerce_fragment(
        payload_fragment: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Normalise a panel ``payload`` fragment into a dict."""
        if not payload_fragment:
            return {}
        if isinstance(payload_fragment, dict):
            return dict(payload_fragment)
        text = str(payload_fragment).strip()
        if not text.startswith("{"):
            # hass_xiaodu's format string sliced off the outer braces; accept
            # a bare fragment as well by wrapping it.
            text = "{" + text + "}"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            _LOGGER.debug("面板 payload 片段无法解析，已忽略: %s", text[:120])
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def async_panel_switch(
        self,
        switch_type: str,
        type_value: Any,
        header_name: str,
        payload_fragment: str | dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Send a panel function directive with the given header name."""
        if not header_name:
            return False, "missing_header_name"
        submit = {
            "header": {
                "namespace": DUEROS_NAMESPACE,
                "name": header_name,
                "payloadVersion": 3,
            },
            "payload": self._build_panel_payload(
                switch_type, type_value, payload_fragment
            ),
        }
        return await self.async_send_command(submit)

    async def async_panel_switch_on(
        self,
        switch_type: str,
        type_value: Any,
        header_name_on: str,
        header_name_off: str = "",
        payload_fragment: str | dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Turn on a panel function switch."""
        return await self.async_panel_switch(
            switch_type, type_value, header_name_on, payload_fragment
        )

    async def async_panel_switch_off(
        self,
        switch_type: str,
        type_value: Any,
        header_name_on: str,
        header_name_off: str = "",
        payload_fragment: str | dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Turn off a panel function switch."""
        return await self.async_panel_switch(
            switch_type, type_value, header_name_off, payload_fragment
        )

    async def async_panel_button(
        self,
        switch_type: str,
        header_name: str,
        payload_fragment: str | dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Fire a panel button (e.g. clothes-rack up / down)."""
        if not header_name:
            return False, "missing_header_name"
        aid = self.appliance_id
        body: dict[str, Any] = {
            "applianceId": aid,
            "parameters": {
                "attribute": switch_type,
                "proxyConnectStatus": False,
            },
            "appliance": {"applianceId": [aid]},
            switch_type: {},
        }
        fragment = self._coerce_fragment(payload_fragment)
        if fragment:
            body.update(fragment)
        submit = {
            "header": {
                "namespace": DUEROS_NAMESPACE,
                "name": header_name,
                "payloadVersion": 3,
            },
            "payload": body,
        }
        return await self.async_send_command(submit)
