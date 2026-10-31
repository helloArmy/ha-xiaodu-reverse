"""Constants for the XiaoDu Reverse (小度反向接入) integration.

Domain layout
-------------
* ``login.py``  — Baidu wappass account/password login (from RockJesus/ha-xiaodu)
* ``api.py``    — XiaoDu cloud smart-home control (from 2331892928/hass_xiaodu)
"""

from __future__ import annotations

DOMAIN = "xiaodu_reverse"
NAME = "小度反向接入"
MANUFACTURER = "Baidu"

# ── Config / options keys ───────────────────────────────────────────────
CONF_COOKIE = "cookie"
CONF_HOUSE_ID = "house_id"
CONF_HOUSE_NAME = "house_name"
CONF_DEVICES = "devices"
CONF_APPLIANCE_TYPES = "appliance_types"
CONF_SCAN_INTERVAL = "scan_interval"

CONF_LOGIN_MODE = "login_mode"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_BAIDUID = "baiduid"
CONF_MOBILE = "mobile"
CONF_VERIFYCODE = "verifycode"
CONF_CODESTRING = "codestring"
CONF_DEVICE_IDS = "device_ids"

# ── Login modes ─────────────────────────────────────────────────────────
LOGIN_MODE_PASSWORD = "password"
LOGIN_MODE_COOKIE = "cookie"

# ── Polling ─────────────────────────────────────────────────────────────
DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600

# ── XiaoDu cloud endpoints ──────────────────────────────────────────────
XIAODU_HOST = "https://xiaodu.baidu.com"
API_GATEWAY = "/appserver/gateway/app/v1"
API_MULTIHOUSE = "/saiya/smarthome/multihouse"
API_APPLIANCE = "/saiya/smarthome/appliance"
API_DEVICE_LIST = "/saiya/smarthome/devicelist"
API_APPLIANCE_DETAILS = "/saiya/smarthome/appliancedetails"
API_DIRECTIVE_SEND = "/saiya/smarthome/directivesend"

HOUSE_ID_COOKIE = "HOUSE_ID"

# DuerOS connected-home control namespace
DUEROS_NAMESPACE = "DuerOS.ConnectedHome.Control"

# ── Error / abort reasons ───────────────────────────────────────────────
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_UNKNOWN = "unknown"
ERROR_LOGIN_FAILED = "login_failed"
ERROR_LOGIN_BLOCKED = "login_blocked"
ERROR_CAPTCHA_REQUIRED = "captcha_required"

ABORT_NO_HOUSE = "no_house_found"
ABORT_NO_DEVICE = "no_device_selected"
ABORT_REAUTH = "reauth_aborted"

# ── Panel titles that carry per-function switch lists ───────────────────
# XieDu exposes multi-function appliances (clothes rack, air cooler, ...)
# as ``panels``; the "功能控制" panel holds the toggle list.
PANEL_TITLE_FUNCTIONS = ("功能控制", "功能", "开关", "Function")

# ── SMS verification (config flow) ──────────────────────────────────────
CONF_ACTION = "action"
SMS_ACTION_SEND = "send"
SMS_ACTION_SUBMIT = "submit"
# 百度侧 60 秒内重复下发会失败，这里做同样的限制
SMS_RESEND_INTERVAL = 60
ERROR_RESEND_TOO_SOON = "resend_too_soon"
ERROR_CODE_REQUIRED = "code_required"
ERROR_SMS_FAILED = "sms_send_failed"
ERROR_SESSION_EXPIRED = "session_expired"
