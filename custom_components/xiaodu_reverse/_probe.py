"""HA 运行环境探测（临时模块，用于验证容器内能否运行 Chromium）。

把探测结果写入 HA 日志（前缀 ``XD-PROBE``），便于远程读取。
探测完成后在 ``/config/.xiaodu_reverse/probe.json`` 留下标记，避免每次重启重跑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PROBE_VERSION = 2
CFT_URL = (
    "https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.80"
    "/linux64/chrome-headless-shell-linux64.zip"
)
BROWSER_BINARIES = (
    "chrome-headless-shell",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)
# chrome-headless-shell 在 Debian 上的共享库依赖
CHROMIUM_LIBS = (
    "libnss3.so",
    "libnspr4.so",
    "libnssutil3.so",
    "libsmime3.so",
    "libatk-1.0.so.0",
    "libatk-bridge-2.0.so.0",
    "libcups.so.2",
    "libdrm.so.2",
    "libgbm.so.1",
    "libxkbcommon.so.0",
    "libpango-1.0.so.0",
    "libcairo.so.2",
    "libasound.so.2",
    "libatspi.so.0",
    "libdbus-1.so.3",
    "libexpat.so.1",
    "libglib-2.0.so.0",
    "libX11.so.6",
    "libXcomposite.so.1",
    "libXdamage.so.1",
    "libXext.so.6",
    "libXfixes.so.3",
    "libXrandr.so.2",
)


def _log(message: str) -> None:
    """Emit one probe line (WARNING so it lands in home-assistant.log)."""
    _LOGGER.warning("XD-PROBE %s", message)


def _installed_libs() -> tuple[set[str], str]:
    """Return the set of shared libraries known to ldconfig."""
    ldconfig = shutil.which("ldconfig") or shutil.which("/sbin/ldconfig")
    if not ldconfig:
        return set(), "no-ldconfig"
    try:
        proc = subprocess.run(
            [ldconfig, "-p"], capture_output=True, text=True, timeout=60, check=False
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return set(), f"ldconfig-failed:{type(exc).__name__}"
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        if "=>" not in line:
            continue
        left = line.split("=>", 1)[0].strip()
        if not left:
            continue
        names.add(left.split(" ")[0])
    return names, "ok"


def _check_libs() -> dict[str, Any]:
    """Check which Chromium dependencies are present."""
    present, how = _installed_libs()
    missing = [lib for lib in CHROMIUM_LIBS if lib not in present]
    return {
        "method": how,
        "total_known": len(present),
        "missing": missing,
        "missing_count": len(missing),
    }


async def _download(session, url: str, target: Path) -> dict[str, Any]:
    """Stream-download the browser archive."""
    import time

    import aiohttp

    started = time.monotonic()
    size = 0
    timeout = aiohttp.ClientTimeout(total=1800, sock_read=180)
    async with session.get(url, timeout=timeout) as resp:
        if resp.status != 200:
            return {"ok": False, "status": resp.status}
        with target.open("wb") as handle:
            async for chunk in resp.content.iter_chunked(1 << 16):
                handle.write(chunk)
                size += len(chunk)
    return {
        "ok": True,
        "bytes": size,
        "seconds": round(time.monotonic() - started, 1),
    }


def _extract(zip_path: Path, dest: Path) -> Path | None:
    """Extract the whole archive and return the browser binary path.

    The headless shell needs its sibling resources (``icudtl.dat``,
    ``v8_context_snapshot.bin``, ``libEGL.so`` …), so the archive must be
    unpacked as a whole rather than picking the executable only.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    return _find_binary(dest)


def _find_binary(root: Path) -> Path | None:
    """Locate the browser executable inside an unpacked archive."""
    if not root.exists():
        return None
    for candidate in root.rglob("chrome-headless-shell"):
        if candidate.is_file():
            try:
                candidate.chmod(0o755)
            except Exception:  # noqa: BLE001
                pass
            return candidate
    return None


def _ldd_missing(exe: Path) -> dict[str, Any]:
    """Report shared libraries that ``ldd`` cannot resolve."""
    ldd = shutil.which("ldd")
    if not ldd:
        return {"ok": False, "error": "no-ldd"}
    try:
        proc = subprocess.run(
            [ldd, str(exe)], capture_output=True, text=True, timeout=90, check=False
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    missing = [
        line.split("=>", 1)[0].strip()
        for line in proc.stdout.splitlines()
        if "not found" in line
    ]
    return {
        "ok": True,
        "missing": missing,
        "count": len(missing),
        "stderr": (proc.stderr or "").strip()[:200],
    }


def _run_version(exe: Path) -> dict[str, Any]:
    """Run ``--version`` to confirm the binary is loadable."""
    try:
        proc = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:120],
        "stderr": (proc.stderr or "").strip()[:600],
    }


def _run_dump_dom(exe: Path, url: str, profile: Path) -> dict[str, Any]:
    """Render a page and dump its DOM (proves JS execution + networking)."""
    try:
        proc = subprocess.run(
            [
                str(exe),
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=8000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    dom = proc.stdout or ""
    return {
        "ok": proc.returncode == 0 and len(dom) > 200,
        "code": proc.returncode,
        "dom_len": len(dom),
        "has_baidu": "百度" in dom or "baidu" in dom.lower(),
        "stderr": (proc.stderr or "").strip()[:400],
    }


async def _cdp_inspect(
    hass: HomeAssistant, session, exe: Path, profile: Path
) -> dict[str, Any]:
    """Boot the browser with CDP and inspect the XiaoDu login page."""
    port = 9222
    try:
        proc = await asyncio.create_subprocess_exec(
            str(exe),
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--headless",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--window-size=1280,900",
            "--lang=zh-CN",
            "about:blank",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"spawn: {type(exc).__name__}: {exc}"}

    result: dict[str, Any] = {"ok": False}
    try:
        # Wait for the DevTools endpoint.
        ws_url = None
        targets = []
        probe_session = session
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                async with probe_session.get(
                    f"http://127.0.0.1:{port}/json/list", timeout=5
                ) as resp:
                    targets = await resp.json()
            except Exception:  # noqa: BLE001
                continue
            for item in targets:
                if item.get("type") == "page" and item.get("webSocketDebuggerUrl"):
                    ws_url = item["webSocketDebuggerUrl"]
                    break
            if ws_url:
                break
        if not ws_url:
            result["error"] = "no-devtools-endpoint"
            return result

        async with probe_session.ws_connect(ws_url, timeout=30, max_msg_size=0) as ws:
            counter = 0

            async def call(method: str, **params) -> dict:
                nonlocal counter
                counter += 1
                current = counter
                await ws.send_json(
                    {"id": current, "method": method, "params": params}
                )
                while True:
                    raw = await asyncio.wait_for(ws.receive(), timeout=90)
                    if raw.type.name != "TEXT":
                        continue
                    message = json.loads(raw.data)
                    if message.get("id") == current:
                        return message.get("result") or {}

            async def evaluate(expression: str) -> Any:
                out = await call(
                    "Runtime.evaluate",
                    expression=expression,
                    returnByValue=True,
                    awaitPromise=True,
                )
                return (out.get("result") or {}).get("value")

            await call("Page.enable")
            await call("Runtime.enable")
            await call(
                "Page.navigate",
                url="https://xiaodu.baidu.com/saiya/smarthome/index.html",
            )
            await asyncio.sleep(12)
            result.update(
                {
                    "ok": True,
                    "url": (await evaluate("location.href") or "")[:160],
                    "title": (await evaluate("document.title") or "")[:80],
                    "body_len": await evaluate(
                        "document.body ? document.body.innerText.length : 0"
                    ),
                    "ua": (await evaluate("navigator.userAgent") or "")[:120],
                    "webdriver": await evaluate("navigator.webdriver"),
                    "has_scan_tab": await evaluate(
                        "(() => {const t=(document.body?document.body.innerText:'');"
                        " return /扫码|扫描二维码/.test(t);})()"
                    ),
                    "has_pwd_tab": await evaluate(
                        "(() => {const t=(document.body?document.body.innerText:'');"
                        " return /用户名登录|账号密码登录/.test(t);})()"
                    ),
                }
            )
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=15)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    return result


async def _run(hass: HomeAssistant, session, base: Path) -> dict[str, Any]:
    """Execute the probe sequence."""
    info: dict[str, Any] = {"version": PROBE_VERSION}

    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform()[:100],
        "machine": platform.machine(),
        "docker": Path("/.dockerenv").exists(),
        "config_dir": hass.config.config_dir,
        "free_mb": round(shutil.disk_usage(base).free / 1048576),
        "euid": os.geteuid() if hasattr(os, "geteuid") else None,
        "apt": shutil.which("apt-get") or "",
        "dpkg": shutil.which("dpkg") or "",
        "tar": shutil.which("tar") or "",
        "ldconfig": shutil.which("ldconfig") or shutil.which("/sbin/ldconfig") or "",
    }
    info["env"] = env
    _log(
        "env python={python} arch={machine} docker={docker} euid={euid} "
        "free={free_mb}MB apt={apt} dpkg={dpkg}".format(**env)
    )

    existing = {name: shutil.which(name) for name in BROWSER_BINARIES if shutil.which(name)}
    info["existing_browsers"] = existing
    _log(f"existing_browsers {existing or 'none'}")

    libs = _check_libs()
    info["libs"] = libs
    _log(
        f"libs method={libs['method']} known={libs['total_known']} "
        f"missing={libs['missing_count']} {','.join(libs['missing'][:20])}"
    )

    unpacked = base / "chromium"
    archive = base / "chs.zip"
    profile = base / "profile"
    exe = _find_binary(unpacked)

    if exe is None:
        if archive.exists() and archive.stat().st_size > 1_000_000:
            _log("archive already downloaded, skipping fetch")
            download = {"ok": True, "cached": True, "bytes": archive.stat().st_size}
        else:
            download = await _download(session, CFT_URL, archive)
        info["download"] = download
        _log(
            "download "
            + (
                f"ok {download['bytes'] / 1048576:.1f}MB"
                + (" (cached)" if download.get("cached") else f" in {download.get('seconds')}s")
                if download.get("ok")
                else f"FAILED {download}"
            )
        )
        if not download.get("ok"):
            return info
        try:
            exe = _extract(archive, unpacked)
            extracted = exe is not None
        except Exception as exc:  # noqa: BLE001
            extracted = False
            info["extract_error"] = f"{type(exc).__name__}: {exc}"
        info["extracted"] = extracted
        _log(f"extract {extracted} -> {exe}")
        if not extracted or exe is None:
            return info
    else:
        _log(f"binary already present {exe}")

    ldd = _ldd_missing(exe)
    info["ldd"] = ldd
    if ldd.get("ok"):
        _log(
            f"ldd missing={ldd['count']} {','.join(ldd['missing'][:20])}"
        )
    else:
        _log(f"ldd unavailable {ldd.get('error')}")

    version = _run_version(exe)
    info["version"] = version
    if version.get("ok"):
        _log(f"binary ok {version.get('stdout')}")
    else:
        _log(
            "binary FAILED code={} error={} stderr={}".format(
                version.get("code"),
                version.get("error"),
                (version.get("stderr") or "").replace("\n", " ")[:300],
            )
        )
    if not version.get("ok"):
        return info

    dump = _run_dump_dom(exe, "https://www.baidu.com/", profile)
    info["dump_dom"] = dump
    _log(
        "dumpdom "
        + (
            f"ok len={dump['dom_len']} baidu={dump['has_baidu']}"
            if dump.get("ok")
            else f"FAILED code={dump.get('code')} err={dump.get('stderr')}"
        )
    )

    cdp = await _cdp_inspect(hass, exe, profile)
    info["cdp"] = cdp
    if cdp.get("ok"):
        _log(
            "cdp ok url={url} title={title} body={body_len} "
            "scan_tab={has_scan_tab} pwd_tab={has_pwd_tab} webdriver={webdriver}".format(
                **cdp
            )
        )
    else:
        _log(f"cdp FAILED {cdp.get('error')}")

    _log("done")
    return info


async def async_probe_once(hass: HomeAssistant, session) -> dict[str, Any]:
    """Run the probe once per PROBE_VERSION, caching the result on disk."""
    base = Path(hass.config.path(".xiaodu_reverse"))
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        _log(f"cannot create {base}: {exc}")
        return {}

    marker = base / "probe.json"
    if marker.exists():
        try:
            cached = json.loads(marker.read_text(encoding="utf-8"))
            if cached.get("version") == PROBE_VERSION:
                return cached
        except Exception:  # noqa: BLE001
            pass

    try:
        info = await _run(hass, session, base)
    except Exception as exc:  # noqa: BLE001
        _log(f"probe crashed {type(exc).__name__}: {exc}")
        info = {"version": PROBE_VERSION, "error": f"{type(exc).__name__}: {exc}"}

    try:
        marker.write_text(
            json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass
    return info
