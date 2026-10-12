# -*- coding: utf-8 -*-
"""Cloudflare Turnstile：定位 iframe、坐标点击、轮询 token，再放行提交。

对齐公开实现里的处理方式：不接打码平台，不调用 turnstile.reset()。
控件里的 token 长度达到阈值后才视为通过。整页挑战点不过去时，
抛出 CloudflareChallengeError，由注册流程停止并提示更换代理。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

TOKEN_MIN_LENGTH = 80
CLICK_OFFSET_X = 24
CF_FIRST_RETRY_AFTER = 3.0
CF_RETRY_INTERVAL = 15.0
CLICK_INTERVAL = 3.0
POLL_INTERVAL = 2.0
CLICK_EVERY_ROUNDS = 4

TOKEN_INPUT_SELECTOR = 'input[name="cf-turnstile-response"]'
WIDGET_SELECTOR = (
    'input[name="cf-turnstile-response"], '
    'iframe[src*="turnstile"], iframe[src*="challenges.cloudflare.com"], '
    'div.cf-turnstile'
)
IFRAME_SELECTOR = 'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'

_READ_TOKEN_JS = r"""
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch (e) {
  return '';
}
"""

_FILL_TOKEN_JS = r"""
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return 0;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
"""

_INTERSTITIAL_JS = r"""
const title = String(document.title || '').toLowerCase();
const text = String((document.body && document.body.innerText) || '').slice(0, 500).toLowerCase();
const href = String(location.href || '').toLowerCase();
const markers = ['just a moment', 'attention required', 'performing security verification', 'security verification', 'verify you are human', 'confirm you are human', 'sicherheitsüberprüfung', 'sicherheitsuberprufung', 'bestätigen sie, dass sie ein mensch sind', '正在验证您是否是真人', 'enable javascript and cookies'];
return markers.some(item => title.includes(item) || text.includes(item))
  || href.includes('__cf_chl')
  || href.includes('/cdn-cgi/challenge');
"""


_INTERSTITIAL_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "performing security verification",
    "security verification",
    "verify you are human",
    "confirm you are human",
    "sicherheitsüberprüfung",
    "sicherheitsuberprufung",
    "bestätigen sie, dass sie ein mensch sind",
    "正在验证您是否是真人",
)


class CloudflareChallengeError(RuntimeError):
    """整页 Cloudflare 挑战未通过，继续提交没有意义。"""


def interstitial_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return "__cf_chl" in lowered or "/cdn-cgi/challenge" in lowered


def interstitial_title(title: str) -> bool:
    lowered = str(title or "").lower()
    return any(marker in lowered for marker in _INTERSTITIAL_TITLE_MARKERS)


def playwright_challenge_visible(page: Any) -> bool:
    """邮箱提交后是否已经落到整页 Cloudflare 验证。

    仍在 chatgpt.com/auth/login 时只看地址，不读 title，也不扫 frame。
    那段 SPA 过渡里 locator / evaluate 会把 Camoufox 内容进程打崩。
    """
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:
        url = ""
    if interstitial_url(url):
        return True
    lowered = url.lower()
    if "chatgpt.com" in lowered and "/auth/login" in lowered:
        return False
    try:
        title = str(page.title() or "")
    except Exception:
        title = ""
    if interstitial_title(title):
        return True
    try:
        return _turnstile_frame(page) is not None
    except Exception:
        return False


def token_ready(token: str | None) -> bool:
    return len(str(token or "").strip()) >= TOKEN_MIN_LENGTH


def should_retry_cf(
    wait_since: float | None,
    last_retry_at: float | None,
    now: float | None = None,
    *,
    first_after: float = CF_FIRST_RETRY_AFTER,
    interval: float = CF_RETRY_INTERVAL,
) -> bool:
    """首次等满 first_after，之后至少间隔 interval 才再做一次完整同步。"""
    if wait_since is None:
        return False
    now = time.time() if now is None else now
    if not last_retry_at or last_retry_at <= 0:
        return (now - wait_since) >= first_after
    return (now - last_retry_at) >= interval


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)
        return
    logger.info("[Turnstile] %s", message)


def _looks_like_playwright_page(page: Any) -> bool:
    if page is None or not callable(getattr(page, "locator", None)):
        return False
    return isinstance(getattr(page, "frames", None), list)


def playwright_page(target: Any) -> Any | None:
    page = getattr(target, "page", None)
    if _looks_like_playwright_page(page):
        return page
    if _looks_like_playwright_page(target):
        return target
    return None


def _frame_url(frame: Any) -> str:
    try:
        return str(getattr(frame, "url", "") or "")
    except Exception:
        return ""


def _is_turnstile_frame(url: str) -> bool:
    lowered = str(url or "").lower()
    return "challenges.cloudflare.com" in lowered or "turnstile" in lowered


def _turnstile_frame(page: Any) -> Any | None:
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:
        return None
    for frame in frames:
        if _is_turnstile_frame(_frame_url(frame)):
            return frame
    return None


def _locator(page: Any, selector: str) -> Any | None:
    try:
        return page.locator(selector)
    except Exception:
        return None


def _locator_count(locator: Any) -> int:
    if locator is None:
        return 0
    try:
        return int(locator.count() or 0)
    except Exception:
        return 0


def read_turnstile_token(target: Any) -> str:
    page = playwright_page(target)
    if page is not None:
        locator = _locator(page, TOKEN_INPUT_SELECTOR)
        if _locator_count(locator) > 0:
            try:
                value = str(locator.first.input_value(timeout=500) or "").strip()
            except Exception:
                value = ""
            if value:
                return value
        if _turnstile_frame(page) is None and _locator_count(_locator(page, WIDGET_SELECTOR)) == 0:
            return ""
        try:
            value = page.evaluate(_READ_TOKEN_JS)
        except Exception:
            value = ""
        return str(value or "").strip()
    try:
        value = target.execute_script("return (" + _READ_TOKEN_JS + ");")
    except Exception:
        value = ""
    return str(value or "").strip()


def widget_present(target: Any) -> bool:
    page = playwright_page(target)
    if page is not None:
        if _turnstile_frame(page) is not None:
            return True
        return _locator_count(_locator(page, WIDGET_SELECTOR)) > 0
    try:
        from selenium.webdriver.common.by import By
        return bool(target.find_elements(By.CSS_SELECTOR, WIDGET_SELECTOR))
    except Exception:
        return False


def cloudflare_interstitial(target: Any) -> bool:
    """父页面本身是 Cloudflare 挑战页，而不是业务页里嵌的 Turnstile。"""
    page = playwright_page(target)
    if page is not None:
        try:
            url = str(getattr(page, "url", "") or "").lower()
        except Exception:
            url = ""
        if interstitial_url(url):
            return True
        try:
            title = str(page.title() or "")
        except Exception:
            title = ""
        return interstitial_title(title)
    try:
        current = str(getattr(target, "current_url", "") or "")
    except Exception:
        current = ""
    if interstitial_url(current):
        return True
    try:
        return bool(target.execute_script("return (" + _INTERSTITIAL_JS + ");"))
    except Exception:
        return False


def _remember_clearance(target: Any) -> str:
    clearance = ""
    page = playwright_page(target)
    cookies: list[Any] = []
    try:
        if page is not None:
            cookies = list(page.context.cookies() or [])
        else:
            cookies = list(target.get_cookies() or [])
    except Exception:
        cookies = []
    for item in cookies:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") == "cf_clearance" and item.get("value"):
            clearance = str(item.get("value") or "")
            break
    if clearance:
        try:
            setattr(target, "cf_clearance", clearance)
        except Exception:
            pass
    return clearance


def fill_turnstile_token(target: Any, token: str) -> int:
    token = str(token or "").strip()
    if not token_ready(token):
        return 0
    page = playwright_page(target)
    script = """
(token) => {
  const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
  if (!cfInput || !token) return 0;
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  if (nativeSetter) nativeSetter.call(cfInput, token);
  else cfInput.value = token;
  cfInput.dispatchEvent(new Event('input', { bubbles: true }));
  cfInput.dispatchEvent(new Event('change', { bubbles: true }));
  return String(cfInput.value || '').trim().length;
}
"""
    try:
        if page is not None:
            filled = page.evaluate(script, token)
        else:
            filled = target.execute_script(_FILL_TOKEN_JS, token)
    except Exception:
        return len(token)
    try:
        return int(filled or 0)
    except (TypeError, ValueError):
        return len(token)


def _frame_click_point(frame: Any) -> tuple[float, float]:
    height = 0.0
    try:
        info = frame.evaluate(
            """() => {
              const b = document.body;
              if (!b) return null;
              const r = b.getBoundingClientRect();
              return {w: r.width, h: r.height};
            }"""
        )
        height = float((info or {}).get("h") or 0)
    except Exception:
        height = 0.0
    if height <= 0:
        height = 60.0
    return float(CLICK_OFFSET_X), height / 2


def click_turnstile(target: Any, log: Callable[[str], None] | None = None) -> bool:
    """点 Turnstile iframe 左侧复选框区域。frame 内点击失败时退到页面坐标。"""
    page = playwright_page(target)
    if page is not None:
        frame = _turnstile_frame(page)
        if frame is not None:
            try:
                click_x, click_y = _frame_click_point(frame)
                frame.click("body", position={"x": click_x, "y": click_y}, timeout=3000)
                _log(log, f"已点击 Turnstile frame body ({click_x:.0f}, {click_y:.0f})")
                return True
            except Exception as exc:
                _log(log, f"Turnstile frame 点击失败: {exc}")
        locator = _locator(page, IFRAME_SELECTOR)
        try:
            box = None if locator is None else locator.first.bounding_box(timeout=1000)
        except Exception:
            box = None
        if box and float(box.get("width") or 0) > 0:
            px = float(box["x"]) + CLICK_OFFSET_X
            py = float(box["y"]) + float(box["height"]) / 2
            try:
                page.mouse.click(px, py)
                _log(log, f"已在 page 级点击 Turnstile iframe ({px:.0f}, {py:.0f})")
                return True
            except Exception as exc:
                _log(log, f"Turnstile page 级点击失败: {exc}")
        return False

    try:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.by import By
    except Exception as exc:
        _log(log, f"Turnstile 点击失败：缺少 Selenium: {exc}")
        return False
    try:
        frames = target.find_elements(By.CSS_SELECTOR, IFRAME_SELECTOR)
    except Exception:
        frames = []
    if not frames:
        _log(log, "Turnstile frame 未找到")
        return False
    element = frames[0]
    try:
        width = float((getattr(element, "size", {}) or {}).get("width") or 0)
        offset_x = CLICK_OFFSET_X - (width / 2 if width else 0)
        ActionChains(target).move_to_element_with_offset(element, offset_x, 0).click().perform()
        _log(log, f"已点击 Turnstile iframe（相对中心偏移 {offset_x:.0f}, 0）")
        return True
    except Exception as exc:
        _log(log, f"Turnstile iframe 点击失败: {exc}")
        return False


def _challenge_timeout(target: Any) -> None:
    clearance = _remember_clearance(target)
    extra = "，已有 cf_clearance" if clearance else ""
    raise CloudflareChallengeError(f"Cloudflare 验证未通过，请更换当前 proxy 后重试{extra}")


def sync_turnstile(
    target: Any,
    *,
    timeout: float = 40.0,
    log: Callable[[str], None] | None = None,
) -> str:
    """点击并轮询，直到 token 就绪或超时。"""
    deadline = time.time() + max(float(timeout), 0.0)
    click_attempted = False
    last_click_round = -100
    round_index = 0
    while time.time() < deadline:
        token = read_turnstile_token(target)
        if token_ready(token):
            fill_turnstile_token(target, token)
            _remember_clearance(target)
            _log(log, f"Turnstile 已通过，token长度={len(token)}")
            return token
        present = widget_present(target)
        blocked = cloudflare_interstitial(target)
        if not present and not blocked:
            return ""
        if not click_attempted or (round_index - last_click_round >= CLICK_EVERY_ROUNDS):
            if not click_attempted:
                _log(log, "尝试点击 Turnstile...")
            else:
                _log(log, "再次尝试点击 Turnstile...")
            click_turnstile(target, log=log)
            click_attempted = True
            last_click_round = round_index
            time.sleep(min(CLICK_INTERVAL, max(0.0, deadline - time.time())))
            round_index += 1
            continue
        time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.time())))
        round_index += 1
    token = read_turnstile_token(target)
    if token_ready(token):
        fill_turnstile_token(target, token)
        _remember_clearance(target)
        _log(log, f"Turnstile 已通过，token长度={len(token)}")
        return token
    if widget_present(target) or cloudflare_interstitial(target):
        _challenge_timeout(target)
    return ""


def ensure_turnstile(
    target: Any,
    *,
    timeout: float = 20.0,
    log: Callable[[str], None] | None = None,
) -> str:
    """提交前调用。没有控件时立刻返回；有控件则等到 token。"""
    token = read_turnstile_token(target)
    if token_ready(token):
        fill_turnstile_token(target, token)
        _remember_clearance(target)
        return token
    if not widget_present(target) and not cloudflare_interstitial(target):
        return ""
    return sync_turnstile(target, timeout=timeout, log=log)


def poke_turnstile(
    target: Any,
    *,
    log: Callable[[str], None] | None = None,
    give_up_after: float = 18.0,
) -> str:
    """等待跳转时轻点一次，避免每次都进入完整阻塞流程。"""
    state = getattr(target, "_turnstile_poke", None)
    if not isinstance(state, dict):
        state = {"since": None, "last_click": 0.0, "last_probe": 0.0, "active": False}
        try:
            setattr(target, "_turnstile_poke", state)
        except Exception:
            state = {"since": None, "last_click": 0.0, "last_probe": 0.0, "active": False}
    now = time.time()
    if not state.get("active") and now - float(state.get("last_probe") or 0) < CLICK_INTERVAL:
        return ""
    state["last_probe"] = now
    token = read_turnstile_token(target)
    if token_ready(token):
        return token
    present = widget_present(target)
    blocked = cloudflare_interstitial(target) if not present else False
    state["active"] = bool(present or blocked)
    if not state["active"]:
        return ""
    if state.get("since") is None:
        state["since"] = now
    if now - float(state.get("last_click") or 0) >= CLICK_INTERVAL:
        click_turnstile(target, log=log)
        state["last_click"] = now
    token = read_turnstile_token(target)
    if token_ready(token):
        fill_turnstile_token(target, token)
        _remember_clearance(target)
        _log(log, f"Turnstile 已通过，token长度={len(token)}")
        return token
    if now - float(state.get("since") or now) >= give_up_after:
        _challenge_timeout(target)
    return ""
