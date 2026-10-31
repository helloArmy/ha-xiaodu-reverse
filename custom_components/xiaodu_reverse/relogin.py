"""BDUSS 自动续期：账密登录 → 短信验证 → loginproxy 换票。

与参考项目用的 wappass 移动 SDK 路径不同，真实浏览器（PC）走的是：

    GET  /v2/api/?getapi                  → token
    GET  /v2/getpublickey                 → 动态 RSA 公钥（需 moonshadV3 签名）
    POST /v2/api/?login                   → 45 个参数；err_no=120021 表示要二次验证
    POST /v3/api/auth/widget              → action=get / send(form) / check(JSON)
    GET  <loginproxy>&apiver=v3&tt=<ms>   → Set-Cookie: BDUSS

最后一步是关键：验证通过（``status=authed``）后**不需要重新登录**，浏览器是把
120021 响应里的 ``loginproxy`` 当成 JSONP 地址再请求一次，服务端就在这一步下发
BDUSS。裸请求该地址会 302 到 error.html，必须补 ``apiver=v3`` 与 ``tt``。

对外只有 ``async_relogin()`` 一个入口：

    bduss = await BaiduRelogin(session, user, pwd).async_relogin(get_code)

其中 ``get_code`` 是一个无参协程，返回用户输入的短信验证码。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from .login import BaiduLogin

_LOGGER = logging.getLogger(__name__)

_PASSPORT = "https://passport.baidu.com"
_WAPPASS = "https://wappass.baidu.com"
_WIDGET = f"{_PASSPORT}/v3/api/auth/widget?bankcheck=1"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_LOGIN_REFERER = (
    "https://passport.baidu.com/v2/?login&tpl=mn&u=https%3A%2F%2Fwww.baidu.com%2F"
)

# 真浏览器实测的 dv 事件值。version=26 / browserInfo="1,2,156" /
# location=",undefined" / screenInfo 是 1280x900 窗口下的真实取样，
# 与官方 dv/g.min.js 的输出逐字节一致（见 tools 侧的 dv_node.js 对拍）。
_DV_EVENTS: dict[str, Any] = {
    "version": 26,
    "browserInfo": "1,2,156",
    "location": ",undefined",
    "screenInfo": "0,0,1249,749,800,600,800,1280,900",
}

# 需要二次验证时 passport 返回的 err_no
ERR_NEED_VERIFY = "120021"
ERR_CAPTCHA = {"340002", "500001", "500002", "50020", "50021", "50052"}


class ReloginError(Exception):
    """自动续期失败。"""


class ReloginNeedsCode(ReloginError):
    """需要用户输入短信验证码。

    带上 ``authid`` 便于调用方把该会话透传给后续步骤。
    """

    def __init__(self, message: str, authid: str = "", mobile: str = "") -> None:
        """Initialize the error."""
        super().__init__(message)
        self.authid = authid
        self.mobile = mobile


class ReloginRejected(ReloginError):
    """百度明确拒绝（密码错误、风控、验证码不正确等）。"""


# ── dv 设备指纹编码（移植自 passport 的 dv/g.min.js） ───────────────────────

_ORDER = {
    "flashInfo": 0,
    "mouseDown": 1,
    "keyDown": 2,
    "mouseMove": 3,
    "version": 4,
    "loadTime": 5,
    "browserInfo": 6,
    "token": 7,
    "location": 8,
    "screenInfo": 9,
}


def _charset(no_digits: bool = False) -> list[str]:
    """dv 用的 64 字符表：数字、大写、小写、'-'、'~'。"""
    ranges = [(48, 57), (65, 90), (97, 122), (45, 45), (126, 126)]
    if no_digits:
        ranges = ranges[1:]
    out: list[str] = []
    for lo, hi in ranges:
        out.extend(chr(code) for code in range(lo, hi + 1))
    return out


def _permute(table: list[str], key: str) -> list[str]:
    """用 key 的字符码对字符表做原地交换。"""
    arr = list(table)
    seed = list(key or "x")
    for i in range(len(arr)):
        idx = ord(seed[i % len(seed)]) % len(arr)
        arr[i], arr[idx] = arr[idx], arr[i]
    return arr


def _to_int(value: float, table: list[str]) -> str:
    """整数按低位在前转成字符串。"""
    val = abs(int(value))
    if not val:
        return table[0]
    out = ""
    while val:
        out += table[val % len(table)]
        val //= len(table)
    return out


class _Encoder:
    """dv 编码器。"""

    def __init__(self, token: str) -> None:
        """Build the two substitution tables from the token."""
        self.dict = _permute(_charset(), token)
        self.dict2 = _permute(_charset(no_digits=True), token)

    def int(self, value: float) -> str:
        """Encode a single integer."""
        return _to_int(value, self.dict)

    def iary(self, values: list[int]) -> str:
        """Encode a small integer array."""
        out = ""
        for val in values:
            text = _to_int(val, self.dict2)
            out += (str(len(text) - 2) + text) if len(text) > 1 else text
        return out

    def bary(self, values: list[int]) -> str:
        """Encode a bit array (6 bits per output char)."""
        top = max(values) if values else 0
        flags = {val for val in values if val > 0}
        count = top // 6 + (1 if top % 6 else 0)
        out = ""
        for block in range(count):
            byte = 0
            for bit in range(6):
                if block * 6 + bit in flags:
                    byte += 2**bit
            out += self.dict[byte]
        return out

    def str(self, text: str) -> str:  # noqa: A003 - 与 JS 原型保持一致
        """Encode a UTF-8 string."""
        raw = text.encode("utf-8")
        table = self.dict
        out = ""
        i = 0
        total = len(raw)
        while i < total:
            x = raw[i]
            i += 1
            if i >= total:
                out += table[x >> 2] + table[(3 & x) << 4] + "__"
                break
            y = raw[i]
            i += 1
            if i >= total:
                out += table[x >> 2] + table[((3 & x) << 4) | ((240 & y) >> 4)]
                out += table[(15 & y) << 2] + "_"
                break
            z = raw[i]
            i += 1
            out += table[x >> 2]
            out += table[((3 & x) << 4) | ((240 & y) >> 4)]
            out += table[((15 & y) << 2) | ((192 & z) >> 6)]
            out += table[63 & z]
        return out


def build_dv() -> str:
    """生成可直接放进登录表单的 dv 值。"""
    token = "tk" + repr(random.random()) + str(int(time.time() * 1000))
    events = dict(_DV_EVENTS)
    events["loadTime"] = int(time.time())
    events["token"] = token

    enc = _Encoder(token)
    parts = [enc.iary([2])]
    for key, value in events.items():
        if key not in _ORDER or value is None:
            continue
        if isinstance(value, bool):
            kind, encoded = 3, enc.int(1 if value else 0)
        elif isinstance(value, (int, float)):
            kind = 1 if value >= 0 else 2
            encoded = enc.int(value)
        elif isinstance(value, (list, tuple)):
            kind, encoded = 4, enc.bary(list(value))
        else:
            kind, encoded = 0, enc.str(str(value))
        if encoded:
            parts.append(enc.iary([_ORDER[key], kind, len(encoded)]) + encoded)
    return token + "@" + "".join(parts)


# ── 小工具 ─────────────────────────────────────────────────────────────────


def _rsa_encrypt(pubkey_pem: str, plain: str) -> str:
    """用百度动态下发的 RSA 公钥做 PKCS1v15 加密（crypttype=12）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_public_key(pubkey_pem.encode())
    return base64.b64encode(key.encrypt(plain.encode(), padding.PKCS1v15())).decode()


def _strip_jsonp(text: str) -> dict[str, Any]:
    """剥掉 JSONP 外壳并解析。"""
    body = text.strip()
    match = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", body, re.S)
    if match:
        body = match.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReloginError(f"百度返回了非 JSON 内容：{body[:120]}") from exc


def _pick_bduss(set_cookie: list[str]) -> str:
    """从 Set-Cookie 列表里挑出 BDUSS（同名取最后一次）。"""
    value = ""
    for raw in set_cookie:
        name, _, rest = raw.partition("=")
        if name.strip() == "BDUSS":
            value = rest.split(";")[0].strip()
    return value


def _gen_gid() -> str:
    """生成 passport 的 gid。"""
    return f"{uuid.uuid4().hex[:7]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:12]}".upper()


def _find(pattern: str, text: str) -> str:
    """正则取第一个捕获组。"""
    match = re.search(pattern, text)
    return match.group(1) if match else ""


# ── 主体 ───────────────────────────────────────────────────────────────────


class BaiduRelogin:
    """一次完整的 BDUSS 续期会话。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        baiduid: str = "",
    ) -> None:
        """Initialize the relogin client."""
        self.session = session
        self.username = username
        self.password = password
        self.baiduid = baiduid
        self.authid = ""
        self.ltoken = ""
        self.lstr = ""
        self.loginproxy = ""
        self.mobile = ""
        self._login = BaiduLogin(session)

    # ── 内部 ────────────────────────────────────────────────────────────

    async def _ensure_baiduid(self) -> None:
        """没有 BAIDUID 时访问一次 baidu.com 取 Set-Cookie。"""
        if self.baiduid:
            return
        try:
            async with self.session.get(
                "https://www.baidu.com/", headers={"User-Agent": _USER_AGENT}, timeout=20
            ) as resp:
                for raw in resp.headers.getall("Set-Cookie", []):
                    if raw.startswith("BAIDUID="):
                        self.baiduid = raw.split("=", 1)[1].split(";")[0]
                        break
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.debug("获取 BAIDUID 失败: %s", exc)

    def _headers(self, *, referer: str = _LOGIN_REFERER) -> dict[str, str]:
        """公共请求头。"""
        headers = {
            "User-Agent": _USER_AGENT,
            "Referer": referer,
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if self.baiduid:
            headers["Cookie"] = f"BAIDUID={self.baiduid}"
        return headers

    # ── 第 1 步：账密登录 ───────────────────────────────────────────────

    async def async_begin(self) -> str | None:
        """走账密登录。

        返回 BDUSS 表示直接登上了；返回 None 表示需要二次验证，
        此时 ``authid`` / ``ltoken`` / ``lstr`` / ``loginproxy`` 已就绪。
        """
        await self._ensure_baiduid()
        cookie = f"BAIDUID={self.baiduid}" if self.baiduid else ""

        gid = _gen_gid()
        now_s = int(time.time())
        now_ms = now_s * 1000
        headers = self._headers()

        getapi = (
            f"{_PASSPORT}/v2/api/?getapi&token=&tpl=mn&subpro=&apiver=v3"
            f"&tt={now_ms}&class=login&gid={gid}&loginversion=v4"
            "&logintype=basicLogin&callback=cb1"
        )
        async with self.session.get(getapi, headers=headers, timeout=30) as resp:
            token = _find(r'"token"\s*:\s*"([^"]*)"', await resp.text())

        ds, tk = await self._login._get_viewlog(cookie)
        cap_s, cap_k = await self._login._get_capinit(cookie)

        # 动态公钥：必须带 moonshadV3 签名，否则 errno != 0
        sig_params = {
            "token": "",
            "tpl": "mn",
            "subpro": "",
            "apiver": "v3",
            "tt": str(now_ms),
            "gid": gid,
            "loginversion": "v4",
            "traceid": "",
        }
        sig = await asyncio.to_thread(self._login._moonshad_sig, sig_params, now_s)
        query = {
            **sig_params,
            "time": sig["time"],
            "alg": "v3",
            "sig": sig["sig"],
            "elapsed": sig["elapsed"],
            "shaOne": sig["shaOne"],
            "callback": "cb2",
        }
        pubkey_url = f"{_PASSPORT}/v2/getpublickey?" + "&".join(
            f"{key}={quote(str(val), safe='')}" for key, val in query.items()
        )
        async with self.session.get(pubkey_url, headers=headers, timeout=30) as resp:
            cert = _strip_jsonp(await resp.text())
        if not cert.get("pubkey"):
            raise ReloginError(f"未能取得登录公钥：{str(cert)[:160]}")
        pem = str(cert["pubkey"]).replace("\\n", "\n")

        data = {
            "staticpage": f"{_PASSPORT}/static/passpc-account/html/v3Jump.html",
            "charset": "UTF-8",
            "token": token,
            "tpl": "mn",
            "subpro": "",
            "apiver": "v3",
            "tt": str(int(time.time() * 1000)),
            "codestring": "",
            "safeflg": "0",
            "u": "https://www.baidu.com/",
            "isPhone": "false",
            "detect": "1",
            "gid": gid,
            "quick_user": "0",
            "logintype": "basicLogin",
            "logLoginType": "pc_loginBasic",
            "idc": "",
            "loginmerge": "true",
            "mkey": "",
            "username": self.username,
            "password": await asyncio.to_thread(_rsa_encrypt, pem, self.password),
            "mem_pass": "on",
            "rsakey": str(cert.get("key", "")),
            "crypttype": "12",
            "ppui_logintime": "15000",
            "countrycode": "",
            "fp_uid": "",
            "fp_info": "",
            "loginversion": "v4",
            "supportdv": "1",
            "bdint_sync_cookie": "",
            "ds": ds,
            "tk": tk,
            "s": cap_s,
            "k": cap_k,
            "fuid": self._login._fuid,
            "safecode": "",
            "dv": build_dv(),
        }
        sig2 = await asyncio.to_thread(self._login._moonshad_sig, data, now_s)
        data.update(
            {
                "time": sig2["time"],
                "alg": "v3",
                "sig": sig2["sig"],
                "elapsed": sig2["elapsed"],
                "shaOne": sig2["shaOne"],
                "traceid": "",
                "callback": "parent.bd__pcbs__abc123",
                "rinfo": sig2.get("rinfo", ""),
            }
        )

        async with self.session.post(
            f"{_PASSPORT}/v2/api/?login", data=data, headers=headers, timeout=45
        ) as resp:
            text = await resp.text()
            set_cookie = resp.headers.getall("Set-Cookie", [])

        bduss = _pick_bduss(set_cookie)
        err_no = _find(r"err_no=(\d+)", text)
        _LOGGER.debug("登录返回 err_no=%s", err_no or "?")

        if bduss:
            return bduss
        if err_no != ERR_NEED_VERIFY:
            if err_no in ERR_CAPTCHA:
                raise ReloginRejected("百度要求图形验证码，无法自动通过")
            raise ReloginRejected(f"登录被拒绝（err_no={err_no or '未知'}）")

        self.authid = _find(r"authid=([A-Za-z0-9_\-]{40,})", text)
        self.ltoken = _find(r"ltoken=([0-9a-f]{32})", text)
        self.lstr = _find(r"lstr=([A-Za-z0-9%+\-_=/]{20,})", text)
        proxy_match = re.search(r"(https?://[^\s\"'<>]*loginproxy[^\s\"'<>]*)", text)
        if proxy_match:
            self.loginproxy = proxy_match.group(1).replace("&amp;", "&")
        if not self.authid:
            raise ReloginError("触发二次验证，但响应里没有 authid")
        _LOGGER.info("已进入二次验证，authid=%s…", self.authid[:12])
        return None

    # ── 第 2 步：验证控件 ───────────────────────────────────────────────

    async def _widget_form(self, action: str, **auth_params: Any) -> dict[str, Any]:
        """按 form 编码提交（send/get 用这个）。"""
        payload = {
            "tpl": "mn",
            "client": "",
            "clientfrom": "pc",
            "authid": self.authid,
            "authField": "mobile",
            "authType": "check",
            "action": action,
            "authScene": "login_force_verify",
            "callback": "parent.bd__pcbs__abc",
            "authParams": json.dumps(auth_params, separators=(",", ":")),
        }
        if action == "send":
            payload["sup624"] = "1"
        headers = self._headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        async with self.session.post(
            _WIDGET, data=payload, headers=headers, timeout=45
        ) as resp:
            text = await resp.text()
        _LOGGER.debug("widget %s 返回: %s", action, text[:200])
        return _strip_jsonp(text)

    async def _widget_json(self, action: str, **auth_params: Any) -> dict[str, Any]:
        """按 JSON body 提交（check 用这个，authParams 必须是对象）。"""
        payload = {
            "tpl": "mn",
            "client": "",
            "clientfrom": "pc",
            "authid": self.authid,
            "authField": "mobile",
            "authType": "check",
            "action": action,
            "authScene": "login_force_verify",
            "authParams": auth_params,
        }
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        async with self.session.post(
            _WIDGET, data=json.dumps(payload), headers=headers, timeout=45
        ) as resp:
            text = await resp.text()
        _LOGGER.debug("widget %s 返回: %s", action, text[:200])
        return _strip_jsonp(text)

    async def async_get_methods(self) -> dict[str, Any]:
        """查询可用验证方式，顺便记下脱敏手机号。"""
        result = await self._widget_form(
            "get",
            supportThirdList=["google", "twitter"],
            upsms_tel="",
            dxmAuth=0,
            clientPasskeysEnable=0,
        )
        info = (result.get("data") or {}).get("defaultMethodInfo") or {}
        self.mobile = str(info.get("mobile") or info.get("showMobile") or "")
        return result

    async def async_send_code(self) -> dict[str, Any]:
        """触发短信下发（form 编码 + sup624）。"""
        return await self._widget_form(
            "send", banktype="", type="vcode", secret=""
        )

    async def async_submit_code(self, code: str) -> dict[str, Any]:
        """提交短信验证码（JSON body）。"""
        return await self._widget_json(
            "check",
            vcode=code,
            type="vcode",
            bankCardType="",
            secret="",
        )

    @staticmethod
    def _is_authed(result: dict[str, Any]) -> bool:
        """判断验证控件的返回是否表示通过。"""
        if str(result.get("code")) == "110000":
            return True
        process = result.get("processRes") or {}
        return str(process.get("status") or result.get("status") or "") == "authed"

    async def async_verify_code(self, code: str) -> bool:
        """提交短信验证码，返回是否通过。"""
        result = await self.async_submit_code(code)
        return self._is_authed(result)

    # ── 第 3 步：loginproxy 换票 ────────────────────────────────────────

    async def async_exchange_bduss(self) -> str:
        """用已 authed 的会话请求 loginproxy，取 BDUSS。

        浏览器这一步是 ``passport.data.jsonp()``，会自动补 ``apiver=v3`` 与
        ``tt``；缺了它们服务端直接 302 到 error.html。
        """
        if not self.loginproxy:
            raise ReloginError("没有 loginproxy，无法换取 BDUSS")
        url = self.loginproxy + "&" + urlencode(
            {"apiver": "v3", "tt": int(time.time() * 1000)}
        )
        headers = self._headers()
        headers["Accept"] = "*/*"
        async with self.session.get(
            url, headers=headers, timeout=30, allow_redirects=False
        ) as resp:
            text = await resp.text()
            set_cookie = resp.headers.getall("Set-Cookie", [])
            status = resp.status

        bduss = _pick_bduss(set_cookie)
        if bduss:
            return bduss
        if status == 302:
            _LOGGER.debug("loginproxy 被重定向到 %s", resp.headers.get("Location"))
        try:
            parsed = _strip_jsonp(text)
        except ReloginError:
            parsed = {}
        err = parsed.get("errInfo") or {}
        no = str(err.get("no") or "")
        if no == "120018":
            # 实测：验证通过后约 30~45 分钟 authid 失效，必须紧接着换票
            raise ReloginError("验证会话已过期（120018），需要重新登录并立即换取 BDUSS")
        raise ReloginError(
            f"loginproxy 未下发 BDUSS（HTTP {status}，errInfo={no or '无'}）"
        )

    # ── 编排 ────────────────────────────────────────────────────────────

    async def async_relogin(
        self, code_provider: Callable[[], Awaitable[str]]
    ) -> str:
        """跑完整流程，返回新的 BDUSS。

        ``code_provider`` 只在需要短信验证时才被调用，应返回 6 位验证码。
        不需要验证时（BDUSS 直接下发）它不会被调用。
        """
        bduss = await self.async_begin()
        if bduss:
            _LOGGER.info("登录直接成功，无需二次验证")
            return bduss

        try:
            methods = await self.async_get_methods()
            if methods:
                _LOGGER.debug("可用验证方式: %s", str(methods)[:160])
        except ReloginError as exc:
            _LOGGER.debug("查询验证方式失败（继续按短信处理）: %s", exc)

        sent = await self.async_send_code()
        if not (sent.get("send") == "success" or str(sent.get("code")) == "110000"):
            raise ReloginRejected(f"短信下发失败：{str(sent)[:160]}")

        code = (await code_provider()).strip()
        if not code:
            raise ReloginNeedsCode("未收到验证码", self.authid, self.mobile)

        checked = await self.async_submit_code(code)
        if not self._is_authed(checked):
            raise ReloginRejected(f"验证码未通过：{str(checked)[:160]}")

        return await self.async_exchange_bduss()
