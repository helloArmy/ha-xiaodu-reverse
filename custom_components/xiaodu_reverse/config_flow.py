"""Config flow for the XiaoDu Reverse (小度反向接入) integration.

Login follows RockJesus/ha-xiaodu: wappass account + password (with optional
BAIDUID and SMS verification), or a pasted BDUSS cookie.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CannotConnect as ApiCannotConnect
from .api import LoginExpired
from .api import XiaoDuApi
from .const import (
    ABORT_NO_DEVICE,
    ABORT_NO_HOUSE,
    ABORT_REAUTH,
    CONF_APPLIANCE_TYPES,
    CONF_BAIDUID,
    CONF_COOKIE,
    CONF_DEVICE_IDS,
    CONF_DEVICES,
    CONF_HOUSE_ID,
    CONF_HOUSE_NAME,
    CONF_LOGIN_MODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFYCODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_LOGIN_BLOCKED,
    ERROR_LOGIN_FAILED,
    ERROR_UNKNOWN,
    LOGIN_MODE_COOKIE,
    LOGIN_MODE_PASSWORD,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NAME,
)
from .login import (
    BaiduCaptchaRequired,
    BaiduLogin,
    BaiduLoginBlocked,
    BaiduLoginError,
)

_LOGGER = logging.getLogger(__name__)

LOGIN_MODE_LABELS = {
    LOGIN_MODE_PASSWORD: "百度账号密码登录（可自动续期，推荐）",
    LOGIN_MODE_COOKIE: "直接填写 BDUSS Cookie",
}

COOKIE_HELP_URL = "https://xiaodu.baidu.com/saiya/smarthome/index.html"


class XiaoDuReverseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        super().__init__()
        self._cookie: str = ""
        self._house_list: dict[str, str] = {}
        self._house_id: str = ""
        self._house_name: str = ""
        self._device_dict: dict[str, str] = {}
        self._login: BaiduLogin | None = None
        self._session: aiohttp.ClientSession | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._login_mode: str = LOGIN_MODE_PASSWORD
        self._pending_username: str = ""
        self._pending_password: str = ""
        self._pending_baiduid: str = ""
        self._verify_token: str = ""

    # ── session helpers ────────────────────────────────────────────────

    def _get_session(self) -> aiohttp.ClientSession:
        """Return this flow's dedicated session (never the shared one)."""
        if self._session is None or self._session.closed:
            self._session = async_create_clientsession(self.hass)
        return self._session

    def _drop_session(self) -> None:
        """Drop the session reference; HA owns the cleanup."""
        self._session = None

    def async_remove(self) -> None:
        """Release the per-flow session when the flow ends."""
        self._session = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> XiaoDuReverseOptionsFlow:
        """Return the options flow."""
        return XiaoDuReverseOptionsFlow()

    # ── reauth ─────────────────────────────────────────────────────────

    async def async_step_reauth(
        self, entry_data: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-authenticate an existing entry."""
        self._reauth_entry = getattr(self, "_get_reauth_entry", lambda: None)()
        if self._reauth_entry is None:
            return self.async_abort(reason=ABORT_REAUTH)
        return await self.async_step_user()

    # ── step 1: login mode ─────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick the login method."""
        if user_input is not None:
            self._login_mode = user_input[CONF_LOGIN_MODE]
            if self._login_mode == LOGIN_MODE_PASSWORD:
                return await self.async_step_login()
            return await self.async_step_cookie()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOGIN_MODE, default=LOGIN_MODE_PASSWORD
                    ): vol.In(LOGIN_MODE_LABELS),
                }
            ),
        )

    # ── step 2a: account + password ────────────────────────────────────

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Log in with the Baidu account."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = str(user_input.get(CONF_USERNAME, "")).strip()
            password = str(user_input.get(CONF_PASSWORD, ""))
            baiduid = str(user_input.get(CONF_BAIDUID, "")).strip()
            self._pending_username = username
            self._pending_password = password
            self._pending_baiduid = baiduid

            if not username or not password:
                errors["base"] = ERROR_LOGIN_FAILED
            else:
                self._login = BaiduLogin(self._get_session())
                try:
                    result = await self._login.login(
                        username, password, baiduid=baiduid
                    )
                except BaiduLoginBlocked as exc:
                    _LOGGER.warning("小度账密登录被风控拦截: %s", exc)
                    if exc.verify_token:
                        self._verify_token = exc.verify_token
                        sent = await self._login.send_sms_code(
                            username, exc.verify_token, baiduid=baiduid
                        )
                        return await self.async_step_sms_verify(sms_sent=sent)
                    # Device-trust challenge without an SMS token: the only
                    # way through is a browser login + BDUSS cookie.
                    errors["base"] = (
                        "verify_required" if exc.verify_url else ERROR_LOGIN_BLOCKED
                    )
                except BaiduCaptchaRequired as exc:
                    _LOGGER.warning("小度登录需要图形验证码: %s", exc.codestring)
                    errors["base"] = "captcha_required"
                except BaiduLoginError as exc:
                    _LOGGER.warning("小度账密登录失败: %s", exc)
                    errors["base"] = ERROR_LOGIN_FAILED
                else:
                    self._cookie = str(result.get("bduss") or "")
                    if self._reauth_entry is not None:
                        return await self._finish_reauth()
                    return await self._after_auth()

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(CONF_BAIDUID): str,
                }
            ),
            errors=errors,
            description_placeholders={"cookie_url": COOKIE_HELP_URL},
        )

    # ── step 2a2: SMS code ─────────────────────────────────────────────

    async def async_step_sms_verify(
        self,
        user_input: dict[str, Any] | None = None,
        sms_sent: bool | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Collect the SMS verification code and retry the login."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            code = str(user_input.get(CONF_VERIFYCODE, "")).strip()
            if not code:
                errors["base"] = ERROR_LOGIN_FAILED
            elif self._login is None:
                errors["base"] = ERROR_LOGIN_FAILED
            else:
                try:
                    result = await self._login.login(
                        self._pending_username,
                        self._pending_password,
                        baiduid=self._pending_baiduid,
                        verifycode=code,
                        codestring=self._verify_token,
                    )
                except BaiduLoginBlocked as exc:
                    self._verify_token = exc.verify_token or self._verify_token
                    errors["base"] = ERROR_LOGIN_BLOCKED
                    placeholders["detail"] = str(exc)
                except BaiduCaptchaRequired:
                    errors["base"] = "captcha_required"
                except BaiduLoginError as exc:
                    errors["base"] = ERROR_LOGIN_FAILED
                    placeholders["detail"] = str(exc)
                else:
                    self._cookie = str(result.get("bduss") or "")
                    if self._reauth_entry is not None:
                        return await self._finish_reauth()
                    return await self._after_auth()

        if sms_sent:
            placeholders.setdefault("detail", "短信验证码已发送，请查收。")
        else:
            placeholders.setdefault(
                "detail",
                "自动发送短信失败，请先在浏览器或小度 App 登录一次触发短信，再输入验证码。",
            )

        return self.async_show_form(
            step_id="sms_verify",
            data_schema=vol.Schema({vol.Required(CONF_VERIFYCODE): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _finish_reauth(self) -> config_entries.ConfigFlowResult:
        """Persist the refreshed cookie onto the existing entry."""
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason=ABORT_REAUTH)
        new_data = dict(entry.data)
        new_data[CONF_COOKIE] = self._cookie
        new_data[CONF_LOGIN_MODE] = self._login_mode
        if self._pending_username:
            new_data[CONF_USERNAME] = self._pending_username
        if self._pending_password:
            new_data[CONF_PASSWORD] = self._pending_password
        if self._pending_baiduid:
            new_data[CONF_BAIDUID] = self._pending_baiduid
        self.hass.config_entries.async_update_entry(entry, data=new_data)
        self._drop_session()
        return self.async_abort(reason="reauth_successful")

    # ── step 2b: cookie ────────────────────────────────────────────────

    async def async_step_cookie(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Log in with a pasted BDUSS cookie."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie = str(user_input.get(CONF_COOKIE, "")).strip()
            api = XiaoDuApi(cookie=cookie, session=self._get_session())
            valid, error = await api.async_check_session()
            if not valid:
                errors["base"] = {
                    "invalid_auth": ERROR_INVALID_AUTH,
                    "cannot_connect": ERROR_CANNOT_CONNECT,
                }.get(error or "", ERROR_UNKNOWN)
            else:
                self._cookie = cookie
                self._login_mode = LOGIN_MODE_COOKIE
                if self._reauth_entry is not None:
                    return await self._finish_reauth()
                return await self._after_auth()

        return self.async_show_form(
            step_id="cookie",
            data_schema=vol.Schema({vol.Required(CONF_COOKIE): str}),
            errors=errors,
            description_placeholders={"cookie_url": COOKIE_HELP_URL},
        )

    # ── shared: load houses ────────────────────────────────────────────

    async def _after_auth(self) -> config_entries.ConfigFlowResult:
        """Load the house list once authenticated."""
        if not self._cookie:
            return self.async_abort(reason="auth_failed")
        api = XiaoDuApi(cookie=self._cookie, session=self._get_session())
        try:
            self._house_list = await api.async_get_house_list()
        except (LoginExpired, ApiCannotConnect) as exc:
            _LOGGER.warning("获取小度房屋列表失败: %s", exc)
            self._house_list = {}

        if not self._house_list:
            return self.async_abort(reason=ABORT_NO_HOUSE)

        # Single house → skip the选择 step.
        if len(self._house_list) == 1:
            self._house_id, self._house_name = next(iter(self._house_list.items()))
            return await self.async_step_device()

        return await self.async_step_house()

    async def async_step_house(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose the house."""
        if user_input is not None:
            self._house_id = str(user_input[CONF_HOUSE_ID])
            self._house_name = self._house_list.get(self._house_id, "")
            return await self.async_step_device()

        return self.async_show_form(
            step_id="house",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOUSE_ID): vol.In(self._house_list)}
            ),
        )

    # ── shared: choose devices ─────────────────────────────────────────

    async def _load_device_dict(self, house_id: str) -> dict[str, str]:
        """Fetch ``{appliance_id: name}`` for the house."""
        api = XiaoDuApi(cookie=self._cookie, session=self._get_session())
        try:
            return await api.async_get_device_dict(house_id)
        except (LoginExpired, ApiCannotConnect) as exc:
            _LOGGER.warning("获取小度设备列表失败: %s", exc)
            return {}

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose which appliances to bring into Home Assistant."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_ids = list(user_input.get(CONF_DEVICE_IDS) or [])
            if not device_ids:
                errors["base"] = ABORT_NO_DEVICE
            else:
                devices, types = await self._collect_device_metadata(device_ids)
                self._drop_session()
                return self.async_create_entry(
                    title=f"{NAME}: {self._house_name or self._house_id}",
                    data={
                        CONF_COOKIE: self._cookie,
                        CONF_HOUSE_ID: self._house_id,
                        CONF_HOUSE_NAME: self._house_name,
                        CONF_DEVICES: devices,
                        CONF_APPLIANCE_TYPES: types,
                        CONF_LOGIN_MODE: self._login_mode,
                        CONF_USERNAME: self._pending_username,
                        CONF_PASSWORD: self._pending_password,
                        CONF_BAIDUID: self._pending_baiduid,
                    },
                )

        if not self._device_dict:
            self._device_dict = await self._load_device_dict(self._house_id)
        if not self._device_dict:
            return self.async_abort(reason="no_device_found")

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_IDS, default=list(self._device_dict)
                    ): cv.multi_select(self._device_dict),
                }
            ),
            errors=errors,
        )

    async def _collect_device_metadata(
        self, device_ids: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch appliance types for the selected devices."""
        api = XiaoDuApi(cookie=self._cookie, session=self._get_session())
        try:
            appliances = await api.async_get_appliances_by_ids(
                self._house_id, device_ids
            )
        except (LoginExpired, ApiCannotConnect):
            appliances = []

        by_id = {
            str(a.get("applianceId")): a for a in appliances if a.get("applianceId")
        }
        devices: list[dict[str, Any]] = []
        types: list[dict[str, Any]] = []
        for did in device_ids:
            info = by_id.get(did) or {}
            devices.append(
                {
                    "applianceId": did,
                    "friendlyName": info.get("friendlyName")
                    or self._device_dict.get(did, did),
                }
            )
            types.append(
                {
                    "applianceTypes": info.get("applianceTypes") or [],
                    "friendlyName": info.get("friendlyName")
                    or self._device_dict.get(did, did),
                }
            )
        return devices, types


class XiaoDuReverseOptionsFlow(config_entries.OptionsFlow):
    """Options: refresh cookie, re-select devices, change poll interval."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        super().__init__()
        self._device_dict: dict[str, str] | None = None
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the options flow's dedicated session."""
        if self._session is None or self._session.closed:
            self._session = async_create_clientsession(self.hass)
        return self._session

    def _drop_session(self) -> None:
        """Drop the session reference."""
        self._session = None

    def async_remove(self) -> None:
        """Release the per-flow session."""
        self._session = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Options menu."""
        if user_input is not None:
            action = user_input.get("next_step_id")
            if action == "cookie":
                return await self.async_step_cookie()
            if action == "devices":
                return await self.async_step_devices()
            if action == "relogin":
                return await self.async_step_relogin()
            return await self.async_step_scan_interval()

        return self.async_show_menu(
            step_id="init",
            menu_options=["cookie", "relogin", "devices", "scan_interval"],
        )

    async def async_step_relogin(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-run the account/password login to refresh the cookie."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = str(user_input.get(CONF_USERNAME, "")).strip()
            password = str(user_input.get(CONF_PASSWORD, ""))
            baiduid = str(user_input.get(CONF_BAIDUID, "")).strip()
            client = BaiduLogin(self._get_session())
            try:
                result = await client.login(username, password, baiduid=baiduid)
            except BaiduLoginBlocked as exc:
                errors["base"] = ERROR_LOGIN_BLOCKED
                _LOGGER.warning("Options 重新登录被风控拦截: %s", exc)
            except BaiduCaptchaRequired:
                errors["base"] = "captcha_required"
            except BaiduLoginError as exc:
                errors["base"] = ERROR_LOGIN_FAILED
                _LOGGER.warning("Options 重新登录失败: %s", exc)
            else:
                new_data = dict(self.config_entry.data)
                new_data[CONF_COOKIE] = str(result.get("bduss") or "")
                new_data[CONF_USERNAME] = username
                new_data[CONF_PASSWORD] = password
                new_data[CONF_BAIDUID] = baiduid
                new_data[CONF_LOGIN_MODE] = LOGIN_MODE_PASSWORD
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._drop_session()
                await self.hass.config_entries.async_reload(
                    self.config_entry.entry_id
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="relogin",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=self.config_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(
                        CONF_BAIDUID,
                        default=self.config_entry.data.get(CONF_BAIDUID, ""),
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={"cookie_url": COOKIE_HELP_URL},
        )

    async def async_step_cookie(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Refresh the BDUSS cookie by hand."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cookie = str(user_input.get(CONF_COOKIE, "")).strip()
            api = XiaoDuApi(cookie=cookie, session=self._get_session())
            valid, error = await api.async_check_session()
            if not valid:
                errors["base"] = {
                    "invalid_auth": ERROR_INVALID_AUTH,
                    "cannot_connect": ERROR_CANNOT_CONNECT,
                }.get(error or "", ERROR_UNKNOWN)
            else:
                new_data = dict(self.config_entry.data)
                new_data[CONF_COOKIE] = cookie
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._drop_session()
                await self.hass.config_entries.async_reload(
                    self.config_entry.entry_id
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="cookie",
            data_schema=vol.Schema({vol.Required(CONF_COOKIE): str}),
            errors=errors,
            description_placeholders={"cookie_url": COOKIE_HELP_URL},
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-select the devices to expose."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_ids = list(user_input.get(CONF_DEVICE_IDS) or [])
            if not device_ids:
                errors["base"] = ABORT_NO_DEVICE
            else:
                cookie = self.config_entry.data.get(CONF_COOKIE, "")
                house_id = self.config_entry.data.get(CONF_HOUSE_ID, "")
                api = XiaoDuApi(cookie=cookie, session=self._get_session())
                try:
                    appliances = await api.async_get_appliances_by_ids(
                        house_id, device_ids
                    )
                except (LoginExpired, ApiCannotConnect):
                    appliances = []
                by_id = {
                    str(a.get("applianceId")): a
                    for a in appliances
                    if a.get("applianceId")
                }
                devices = []
                types = []
                for did in device_ids:
                    info = by_id.get(did) or {}
                    devices.append(
                        {
                            "applianceId": did,
                            "friendlyName": info.get("friendlyName")
                            or (self._device_dict or {}).get(did, did),
                        }
                    )
                    types.append(
                        {
                            "applianceTypes": info.get("applianceTypes") or [],
                            "friendlyName": info.get("friendlyName")
                            or (self._device_dict or {}).get(did, did),
                        }
                    )

                new_data = dict(self.config_entry.data)
                new_data[CONF_DEVICES] = devices
                new_data[CONF_APPLIANCE_TYPES] = types
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self._drop_session()
                await self.hass.config_entries.async_reload(
                    self.config_entry.entry_id
                )
                return self.async_create_entry(title="", data={})

        if self._device_dict is None:
            cookie = self.config_entry.data.get(CONF_COOKIE, "")
            house_id = self.config_entry.data.get(CONF_HOUSE_ID, "")
            api = XiaoDuApi(cookie=cookie, session=self._get_session())
            try:
                self._device_dict = await api.async_get_device_dict(house_id)
            except (LoginExpired, ApiCannotConnect):
                self._device_dict = {}
            if not self._device_dict:
                return self.async_abort(reason="no_device_found")

        current = [
            d.get("applianceId", "")
            for d in self.config_entry.data.get(CONF_DEVICES, [])
            if d.get("applianceId")
        ]
        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_IDS, default=current
                    ): cv.multi_select(self._device_dict),
                }
            ),
            errors=errors,
        )

    async def async_step_scan_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Change the polling interval."""
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                options={
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                },
            )
            return self.async_create_entry(title="", data={})

        current = int(
            self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        return self.async_show_form(
            step_id="scan_interval",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )
