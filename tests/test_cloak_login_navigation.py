# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloakbrowser_driver import adapt_cloak_proxy
from core.roxy_registration import _safe_get


class _PlaywrightGotoError(Exception):
    """模拟 Cloak/Playwright 的 Page.goto 网络错误，不是 Selenium 异常。"""


class _FakeDriver:
    def __init__(self, errors):
        self.errors = list(errors)
        self.get_calls = []
        self.script_calls = []
        self.current_url = "about:blank"

    def set_page_load_timeout(self, seconds):
        return None

    def set_script_timeout(self, seconds):
        return None

    def get(self, url):
        self.get_calls.append(url)
        if self.errors:
            raise self.errors.pop(0)
        self.current_url = url

    def execute_script(self, script):
        self.script_calls.append(script)
        if "readyState" in script:
            return "complete"
        if "document.body" in script:
            return True
        return None


class CloakLoginNavigationTests(unittest.TestCase):
    def test_safe_get_retries_playwright_connection_reset(self):
        driver = _FakeDriver([
            _PlaywrightGotoError('Page.goto: net::ERR_CONNECTION_RESET at https://chatgpt.com/auth/login'),
        ])
        with patch("core.roxy_registration.time.sleep", return_value=None):
            _safe_get(
                driver,
                "https://chatgpt.com/auth/login",
                timeout=15,
                attempts=2,
                accept_hosts=("chatgpt.com", "auth.openai.com"),
            )
        self.assertEqual(
            driver.get_calls,
            ["https://chatgpt.com/auth/login", "https://chatgpt.com/auth/login"],
        )

    def test_safe_get_retries_playwright_timeout_error(self):
        driver = _FakeDriver([
            TimeoutError('Page.goto: Timeout 30000ms exceeded.\n navigating to "https://chatgpt.com/auth/login"'),
        ])
        with patch("core.roxy_registration.time.sleep", return_value=None):
            _safe_get(driver, "https://chatgpt.com/auth/login", timeout=15, attempts=2)
        self.assertEqual(
            driver.get_calls,
            ["https://chatgpt.com/auth/login", "about:blank", "https://chatgpt.com/auth/login"],
        )

    def test_http_proxy_with_credentials_is_not_rewritten_to_socks5(self):
        proxy = "http://openai.4:secret@8.222.186.217:2260"
        with patch("core.cloakbrowser_driver._cloak_supports_http_proxy_inline_auth", return_value=False):
            adapted = adapt_cloak_proxy(proxy)
        self.assertEqual(adapted, proxy)

    def test_socks5h_is_normalized_to_socks5(self):
        adapted = adapt_cloak_proxy("socks5h://openai.4:secret@8.222.186.217:2260")
        self.assertEqual(adapted, "socks5://openai.4:secret@8.222.186.217:2260")

    def test_authenticated_socks5_stays_socks5_but_warns_on_old_binary(self):
        proxy = "socks5://openai.4:secret@8.222.186.217:2260"
        with patch("core.cloakbrowser_driver._cloak_supports_http_proxy_inline_auth", return_value=False), \
             self.assertLogs("core.cloakbrowser_driver", level="WARNING") as logs:
            adapted = adapt_cloak_proxy(proxy)
        self.assertEqual(adapted, proxy)
        self.assertTrue(any("ERR_NO_SUPPORTED_PROXIES" in message for message in logs.output))

    def test_cloak_registration_opens_login_page_with_safe_get_and_cdp_data_saver(self):
        src = Path("core/cloakbrowser_registration.py").read_text(encoding="utf-8")
        self.assertIn("_safe_get(", src)
        self.assertIn("install_selenium(driver)", src)
        self.assertNotIn('driver.get("https://chatgpt.com/auth/login")', src)
        self.assertNotIn("install_playwright(driver.context)", src)


if __name__ == "__main__":
    unittest.main()
