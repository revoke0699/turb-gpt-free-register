# -*- coding: utf-8 -*-
"""Camoufox 启动封装：返回与 Cloak 相同的 Playwright Selenium 适配层。"""
from __future__ import annotations

import logging
import sys
import tempfile
from typing import Any
from urllib.parse import unquote, urlparse

from config import camoufox as _cfg
from core.cloakbrowser_driver import CloakOpenResult, CloakSeleniumDriver

logger = logging.getLogger(__name__)

_CAMOUFOX_FETCH_HINT = (
    '未安装 camoufox，请执行：pip install "camoufox[geoip]" && python -m camoufox fetch'
)


def import_camoufox():
    """导入 Camoufox 同步 API；缺包或缺 binary 时给出安装提示。"""
    try:
        from camoufox.sync_api import Camoufox
    except ImportError as exc:
        raise RuntimeError(_CAMOUFOX_FETCH_HINT) from exc
    return Camoufox


def _isolate_camoufox_class(camoufox_cls):
    """grok-register IsolatedCamoufox：工作线程里避开 Sync API inside asyncio loop。"""
    if not str(getattr(camoufox_cls, "__module__", "")).startswith("camoufox"):
        return camoufox_cls
    try:
        import asyncio
        from greenlet import greenlet
        from typing import cast as _tcast
        from camoufox.sync_api import NewBrowser
        from playwright._impl._connection import Connection as _PwConnection
        from playwright._impl._greenlets import MainGreenlet as _PwMainGreenlet
        from playwright._impl._object_factory import create_remote_object as _pw_create_remote
        from playwright._impl._playwright import Playwright as _PwImpl
        from playwright._impl._transport import PipeTransport as _PwPipeTransport
        from playwright.sync_api._generated import Playwright as _SyncPlaywright
    except Exception as exc:
        logger.debug("[Camoufox] 无法启用隔离事件循环，继续用默认 Camoufox：%s", exc)
        return camoufox_cls

    class IsolatedCamoufox(camoufox_cls):
        def __enter__(self):
            self._loop = asyncio.new_event_loop()
            self._own_loop = True

            def _greenlet_main():
                self._loop.run_until_complete(self._connection.run_as_sync())

            dispatcher_fiber = _PwMainGreenlet(_greenlet_main)
            self._connection = _PwConnection(
                dispatcher_fiber,
                _pw_create_remote,
                _PwPipeTransport(self._loop),
                self._loop,
            )
            g_self = greenlet.getcurrent()

            def _callback_wrapper(channel_owner):
                playwright_impl = _tcast(_PwImpl, channel_owner)
                self._playwright = _SyncPlaywright(playwright_impl)
                g_self.switch()

            self._connection.call_on_object_with_known_name("Playwright", _callback_wrapper)
            dispatcher_fiber.switch()
            playwright = self._playwright
            playwright.stop = self.__exit__
            try:
                self.browser = NewBrowser(self._playwright, **self.launch_options)
            except BaseException as exc:
                super().__exit__(type(exc), exc, exc.__traceback__)
                raise
            return self.browser

    IsolatedCamoufox.__name__ = "IsolatedCamoufox"
    IsolatedCamoufox.__qualname__ = "IsolatedCamoufox"
    return IsolatedCamoufox


def _detect_camoufox_exe() -> str:
    """只扫磁盘上的 camoufox-bin，不调用 launch_path（那会再查 official/stable）。"""
    try:
        from camoufox.pkgman import INSTALL_DIR, LAUNCH_FILE, OS_NAME
    except Exception:
        return ""
    exe_name = LAUNCH_FILE.get(OS_NAME, "camoufox-bin")
    candidates = [INSTALL_DIR / exe_name]
    browsers = INSTALL_DIR / "browsers"
    if browsers.exists():
        candidates.extend(sorted(browsers.rglob(exe_name)))
    for path in reversed(candidates):
        if path.is_file():
            return str(path)
    return ""


def _ensure_camoufox_active_install() -> None:
    """config 里 official/stable 对不上时，激活 browsers/ 下已有安装。"""
    try:
        from camoufox.pkgman import installed_verstr
        installed_verstr()
        return
    except Exception as exc:
        logger.warning("[Camoufox] official/stable 未就绪：%s，尝试激活本地安装", exc)
    try:
        from camoufox.multiversion import list_installed, set_active
        installed = list_installed()
        if not installed:
            logger.warning("[Camoufox] 本地没有已安装的浏览器目录")
            return
        first = installed[0]
        rel = getattr(first, "relative_path", None) or f"browsers/{first.repo_name}/{first.path.name}"
        set_active(rel)
        logger.info("[Camoufox] 已激活本地浏览器：%s", rel)
    except Exception as exc:
        logger.warning("[Camoufox] 激活本地浏览器失败：%s: %s", type(exc).__name__, exc)


def _detect_ff_version() -> str:
    """从已安装 version.json 读主版本，避免 launch_options 再查 official/stable。"""
    try:
        import json
        from pathlib import Path
        from camoufox.pkgman import INSTALL_DIR
    except Exception:
        return ""
    candidates = [INSTALL_DIR / "version.json"]
    browsers = INSTALL_DIR / "browsers"
    if browsers.exists():
        candidates.extend(sorted(browsers.rglob("version.json")))
    for version_file in candidates:
        if not version_file.is_file():
            continue
        try:
            data = json.loads(version_file.read_text(encoding="utf-8"))
            major = str(data.get("version") or "").split(".", 1)[0]
            if major.isdigit():
                return major
        except Exception:
            continue
    return ""


def adapt_camoufox_proxy(proxy: str | None) -> dict | None:
    """把代理 URL 转成 Playwright/Camoufox 的 proxy dict。"""
    proxy = str(proxy or "").strip()
    if not proxy:
        return None
    proxy = proxy.replace("socks5h://", "socks5://")
    parsed = urlparse(proxy)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy}
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    result: dict[str, str] = {"server": server}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password:
        result["password"] = unquote(parsed.password)
    return result


def _excluded_default_addons() -> list:
    """与 grok-register 省流量模式一样，不加载 Camoufox 自带 uBlock。"""
    try:
        from camoufox.addons import DefaultAddons
        return list(DefaultAddons)
    except Exception:
        return []


def _camoufox_native_block_images() -> bool:
    """省流量拦 image 时用 Camoufox 原生开关，避免 Playwright 全量 route。"""
    try:
        from config import browser as _browser_cfg
        from core.browser_data_saver import configured_resource_types
    except Exception:
        return False
    if not bool(getattr(_browser_cfg, "BROWSER_DATA_SAVER_MODE", False)):
        return False
    return "image" in configured_resource_types()


def _resolve_launch_proxy(proxy: str | None) -> str | None:
    if not bool(getattr(_cfg, "CAMOUFOX_USE_PROXY", True)):
        return None
    if proxy is None:
        try:
            from config.proxy import pick_proxy
            proxy = pick_proxy()
        except Exception:
            proxy = None
    return str(proxy or "").strip() or None


def create_camoufox_options(proxy: str | None = None) -> dict:
    """构建可直接传给 Camoufox(**opts) 的启动参数。"""
    timeout_ms = int(getattr(_cfg, "CAMOUFOX_SELENIUM_TIMEOUT", 90) or 90) * 1000
    opts: dict[str, Any] = {
        "headless": bool(getattr(_cfg, "CAMOUFOX_HEADLESS", False)),
        "humanize": bool(getattr(_cfg, "CAMOUFOX_HUMANIZE", True)),
        "geoip": bool(getattr(_cfg, "CAMOUFOX_GEOIP", True)),
        "block_webrtc": bool(getattr(_cfg, "CAMOUFOX_BLOCK_WEBRTC", True)),
        # ChatGPT 提交邮箱后会跨源跳到 auth.openai.com / Turnstile。
        # grok-register 面向 grok.com 不需要这个；关掉 COOP 否则原标签页会被浏览器丢掉。
        "disable_coop": True,
        "i_know_what_im_doing": True,
        "timeout": timeout_ms,
        "persistent_context": True,
    }
    if _camoufox_native_block_images():
        opts["block_images"] = True
    excluded = _excluded_default_addons()
    if excluded:
        # grok-register 容器默认排除 uBlock 等内置扩展，减少内容进程崩溃面。
        opts["exclude_addons"] = excluded
    locale = str(getattr(_cfg, "CAMOUFOX_LOCALE", "") or "").strip()
    timezone = str(getattr(_cfg, "CAMOUFOX_TIMEZONE", "") or "").strip()
    if not locale and bool(getattr(_cfg, "CAMOUFOX_GEOIP", True)):
        locale = _infer_locale(proxy)
    if locale:
        opts["locale"] = locale
    if timezone:
        opts["config"] = {"timezone": timezone}
    os_name = str(getattr(_cfg, "CAMOUFOX_OS", "") or "").strip().lower()
    if os_name in {"windows", "macos", "linux"}:
        opts["os"] = os_name
    exe_path = _detect_camoufox_exe()
    if exe_path:
        opts["executable_path"] = exe_path
    ff_version = _detect_ff_version()
    if ff_version:
        # 必须是 int。不传的话 Camoufox 0.5 会去读 official/stable，config 异常就报未安装。
        opts["ff_version"] = int(ff_version)
    proxy_dict = adapt_camoufox_proxy(proxy)
    if proxy_dict:
        opts["proxy"] = proxy_dict
    user_data_dir = str(getattr(_cfg, "CAMOUFOX_USER_DATA_DIR", "") or "").strip()
    if user_data_dir:
        opts["user_data_dir"] = user_data_dir
    else:
        opts["user_data_dir"] = tempfile.mkdtemp(prefix="turb-camoufox-")
    return opts


def _infer_locale(proxy: str | None) -> str:
    """用本仓库出口画像补 locale；失败则交给 Camoufox geoip。"""
    try:
        from core.cloakbrowser_driver import _detect_cloak_exit_geo
        from config.browser import build_browser_environment
        geo = _detect_cloak_exit_geo(proxy)
        profile = build_browser_environment(geo)
        return str(profile.get("navigator_language") or "").strip()
    except Exception as exc:
        logger.debug("[Camoufox] 自动推断语言失败：%s: %s", type(exc).__name__, exc)
        return ""


def build_camoufox_driver(proxy: str | None = None) -> tuple[CloakSeleniumDriver, CloakOpenResult]:
    """启动 Camoufox 并返回 Selenium 风格 driver。"""
    proxy_url = _resolve_launch_proxy(proxy)
    _ensure_camoufox_active_install()
    opts = create_camoufox_options(proxy=proxy_url)
    # grok-register 把代理账密直接交给 Camoufox；本机 127.0.0.1 转发会在邮箱提交后的新 TLS 上把标签页打崩。
    if opts.get("geoip") is True and proxy_url:
        try:
            from core.cloakbrowser_driver import _detect_cloak_exit_geo
            geo = _detect_cloak_exit_geo(proxy_url) or {}
            ip = str(geo.get("ip") or "").strip()
            if ip:
                opts["geoip"] = ip
                tz = str(geo.get("timezone") or "").strip()
                if tz:
                    config = dict(opts.get("config") or {})
                    config["timezone"] = tz
                    opts["config"] = config
        except Exception as exc:
            logger.debug("[Camoufox] 出口 IP 预探测失败，保持 geoip=True：%s", exc)
    # Firefox 页面内跳转会丢掉 Proxy-Authorization，上游 407 后标签页直接崩溃。
    # 浏览器只连本机无账密入口，由转发补鉴权。geoip 已换成出口 IP，不再走这层。
    forwarder = None
    try:
        from core.proxy_auth_forwarder import maybe_start_http_auth_forwarder
        forwarder = maybe_start_http_auth_forwarder(proxy_url)
    except Exception as exc:
        logger.warning("[Camoufox] 启动本地代理转发失败，继续把账密交给浏览器：%s: %s", type(exc).__name__, exc)
        forwarder = None
    if forwarder is not None:
        logger.info("[Camoufox] HTTP 代理账密改为本地转发：%s -> %s", (opts.get("proxy") or {}).get("server") or proxy_url, forwarder.local_url)
        opts["proxy"] = {"server": forwarder.local_url}
        prefs = dict(opts.get("firefox_user_prefs") or {})
        prefs["network.proxy.allow_hijacking_localhost"] = True
        prefs["signon.autologin.proxy"] = True
        opts["firefox_user_prefs"] = prefs
    camoufox_cls = _isolate_camoufox_class(import_camoufox())
    instance = camoufox_cls(**opts)
    logger.info(
        "[Camoufox] 启动 Camoufox：headless=%s humanize=%s geoip=%s proxy=%s locale=%s timezone=%s persistent=%s exe=%s ff=%s",
        opts.get("headless"), opts.get("humanize"), opts.get("geoip"),
        (opts.get("proxy") or {}).get("server") or "无",
        opts.get("locale") or "自动/默认",
        (opts.get("config") or {}).get("timezone") or "自动/默认",
        bool(opts.get("user_data_dir")),
        opts.get("executable_path") or "自动",
        opts.get("ff_version") or "自动",
    )
    try:
        browser_or_ctx = instance.__enter__()
    except BaseException:
        try:
            instance.__exit__(*sys.exc_info())
        except Exception:
            pass
        if forwarder is not None:
            try:
                forwarder.stop()
            except Exception:
                pass
        raise

    if hasattr(browser_or_ctx, "new_context"):
        browser = browser_or_ctx
        context = browser.new_context()
        page = context.new_page()
    else:
        context = browser_or_ctx
        browser = getattr(context, "browser", None) or context
        pages = list(getattr(context, "pages", []) or [])
        page = pages[0] if pages else context.new_page()

    driver = CloakSeleniumDriver(browser=browser, context=context, page=page, lifecycle=instance)
    driver._proxy_forwarder = forwarder
    configured_dir = str(getattr(_cfg, "CAMOUFOX_USER_DATA_DIR", "") or "").strip()
    if opts.get("user_data_dir") and not configured_dir:
        driver._temp_profile_dir = opts["user_data_dir"]
    driver._registration_log_prefix = "[Camoufox注册]"
    driver.set_page_load_timeout(int(getattr(_cfg, "CAMOUFOX_SELENIUM_TIMEOUT", 90) or 90))
    return driver, CloakOpenResult(
        profile_id="camoufox",
        raw={"driver": "camoufox", "proxy": proxy_url, "options": {
            k: v for k, v in opts.items() if k != "proxy"
        }},
    )
