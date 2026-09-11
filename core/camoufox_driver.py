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
        "i_know_what_im_doing": True,
        "timeout": timeout_ms,
        "persistent_context": True,
    }
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
    opts = create_camoufox_options(proxy=proxy_url)
    camoufox_cls = import_camoufox()
    instance = camoufox_cls(**opts)
    logger.info(
        "[Camoufox] 启动 Camoufox：headless=%s humanize=%s geoip=%s proxy=%s locale=%s timezone=%s persistent=%s",
        opts.get("headless"), opts.get("humanize"), opts.get("geoip"),
        (opts.get("proxy") or {}).get("server") or "无",
        opts.get("locale") or "自动/默认",
        (opts.get("config") or {}).get("timezone") or "自动/默认",
        bool(opts.get("user_data_dir")),
    )
    try:
        browser_or_ctx = instance.__enter__()
    except BaseException:
        try:
            instance.__exit__(*sys.exc_info())
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
