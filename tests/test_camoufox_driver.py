# -*- coding: utf-8 -*-
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.cloakbrowser_driver import (
    CloakSeleniumDriver,
    import_stealth_browser,
    resolve_stealth_engine,
    stealth_log_tag,
)
from webui import config_editor


class CamoufoxDriverTests(unittest.TestCase):
    def test_camoufox_registration_driver_selects_camoufox_engine(self):
        with patch("config.roxybrowser.REGISTRATION_DRIVER", "camoufox"):
            self.assertEqual(resolve_stealth_engine(), "camoufox")
            self.assertEqual(stealth_log_tag(), "Camoufox")

    def test_cloak_registration_driver_keeps_cloakbrowser_engine(self):
        with patch("config.roxybrowser.REGISTRATION_DRIVER", "cloak"):
            self.assertEqual(resolve_stealth_engine(), "cloakbrowser")
            self.assertEqual(stealth_log_tag(), "Cloak")

    def test_camoufox_keep_open_and_timeout_read_camoufox_config(self):
        from core.cloakbrowser_driver import stealth_keep_browser_open, stealth_selenium_timeout
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="camoufox"), \
             patch("config.camoufox.CAMOUFOX_KEEP_BROWSER_OPEN", True), \
             patch("config.camoufox.CAMOUFOX_SELENIUM_TIMEOUT", 75):
            self.assertTrue(stealth_keep_browser_open())
            self.assertEqual(stealth_selenium_timeout(), 75)

    def test_cloak_keep_open_and_timeout_still_read_cloak_config(self):
        from core.cloakbrowser_driver import stealth_keep_browser_open, stealth_selenium_timeout
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="cloakbrowser"), \
             patch("config.cloakbrowser.CLOAK_KEEP_BROWSER_OPEN", False), \
             patch("config.cloakbrowser.CLOAK_SELENIUM_TIMEOUT", 90):
            self.assertFalse(stealth_keep_browser_open())
            self.assertEqual(stealth_selenium_timeout(), 90)

    def test_import_stealth_browser_does_not_load_cloak_for_camoufox(self):
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="camoufox"):
            with self.assertRaises(RuntimeError):
                import_stealth_browser()

    def test_main_routes_camoufox_to_cloak_registration(self):
        src = Path("main.py").read_text(encoding="utf-8")
        self.assertIn('"camoufox"', src)
        self.assertIn("run_cloak_registration", src)

    def test_codex_oauth_routes_camoufox_with_cloak(self):
        src = Path("core/codex_oauth.py").read_text(encoding="utf-8")
        self.assertRegex(src, r'oauth_driver in \([^)]*"camoufox"')

    def test_webui_exposes_camoufox_driver_choice(self):
        help_text = next(f["help"] for f in config_editor.EDITABLE_FIELDS if f["key"] == "REGISTRATION_DRIVER")
        self.assertIn("camoufox", help_text.lower())
        oauth_help = next(f["help"] for f in config_editor.EDITABLE_FIELDS if f["key"] == "CODEX_OAUTH_DRIVER")
        self.assertIn("camoufox", oauth_help.lower())
        html = Path("webui/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("value: 'camoufox'", html)
        field_keys = {f["key"] for f in config_editor.EDITABLE_FIELDS}
        self.assertIn("CAMOUFOX_HEADLESS", field_keys)
        self.assertIn("CAMOUFOX_BLOCK_WEBRTC", field_keys)

    def test_camoufox_data_saver_uses_playwright_route(self):
        from core.cloakbrowser_driver import install_stealth_data_saver
        saver = MagicMock()
        driver = MagicMock()
        driver.context = object()
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="camoufox"):
            install_stealth_data_saver(saver, driver)
        saver.install_playwright.assert_called_once_with(driver.context, catch_all=False)
        saver.install_selenium.assert_not_called()

    def test_create_camoufox_options_avoids_catch_all_proxy_crash(self):
        from core.camoufox_driver import create_camoufox_options
        with patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-test"), \
             patch("core.camoufox_driver._cfg") as cfg, \
             patch("core.browser_data_saver._cfg.BROWSER_DATA_SAVER_MODE", True), \
             patch("core.browser_data_saver._cfg.BROWSER_DATA_SAVER_BLOCKED_RESOURCE_TYPES", ["image", "media"]):
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = True
            cfg.CAMOUFOX_GEOIP = True
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "en-US"
            cfg.CAMOUFOX_TIMEZONE = ""
            cfg.CAMOUFOX_USE_PROXY = True
            cfg.CAMOUFOX_OS = ""
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            opts = create_camoufox_options(proxy="http://u:p@127.0.0.1:7890")
        self.assertTrue(opts["block_images"])
        self.assertTrue(opts["disable_coop"])

    def test_cloak_data_saver_still_uses_selenium_cdp(self):
        from core.cloakbrowser_driver import install_stealth_data_saver
        saver = MagicMock()
        driver = MagicMock()
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="cloakbrowser"):
            install_stealth_data_saver(saver, driver)
        saver.install_selenium.assert_called_once_with(driver)
        saver.install_playwright.assert_not_called()

    def test_create_camoufox_options_disables_humanize_in_docker(self):
        from core.camoufox_driver import create_camoufox_options
        with patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-docker-h"), \
             patch("core.camoufox_driver.running_in_docker", return_value=True), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = True
            cfg.CAMOUFOX_GEOIP = False
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "en-US"
            cfg.CAMOUFOX_TIMEZONE = ""
            cfg.CAMOUFOX_USE_PROXY = False
            cfg.CAMOUFOX_OS = ""
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            opts = create_camoufox_options(proxy="")
        self.assertFalse(opts["humanize"])

    def test_create_camoufox_options_does_not_override_camoufox_webgl_in_docker(self):
        """grok-register 不钉 os、不加 software webrender，避免和 Camoufox WebGL 指纹打架。"""
        from core.camoufox_driver import create_camoufox_options
        with patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-docker"), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = True
            cfg.CAMOUFOX_GEOIP = False
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "en-US"
            cfg.CAMOUFOX_TIMEZONE = ""
            cfg.CAMOUFOX_USE_PROXY = False
            cfg.CAMOUFOX_OS = ""
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            opts = create_camoufox_options(proxy="")
        self.assertNotIn("os", opts)
        self.assertNotIn("firefox_user_prefs", opts)

    def test_create_camoufox_options_passes_executable_path_like_grok_register(self):
        from core.camoufox_driver import create_camoufox_options
        with patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-exe"), \
             patch("core.camoufox_driver._detect_camoufox_exe", return_value="/root/.cache/camoufox/camoufox-bin"), \
             patch("core.camoufox_driver._detect_ff_version", return_value="152"), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = True
            cfg.CAMOUFOX_GEOIP = False
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "en-US"
            cfg.CAMOUFOX_TIMEZONE = ""
            cfg.CAMOUFOX_USE_PROXY = False
            cfg.CAMOUFOX_OS = ""
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            opts = create_camoufox_options(proxy="")
        self.assertEqual(opts["executable_path"], "/root/.cache/camoufox/camoufox-bin")
        self.assertEqual(opts["ff_version"], 152)

    def test_ensure_camoufox_active_install_activates_local_browser(self):
        import sys
        import types
        from core.camoufox_driver import _ensure_camoufox_active_install

        class _Installed:
            repo_name = "official"
            path = type("P", (), {"name": "152.0.4-beta.30-abc"})()

        pkgman = types.ModuleType("camoufox.pkgman")
        pkgman.installed_verstr = MagicMock(side_effect=RuntimeError("official/stable is not installed"))
        multi = types.ModuleType("camoufox.multiversion")
        multi.list_installed = MagicMock(return_value=[_Installed()])
        multi.set_active = MagicMock()
        camoufox = types.ModuleType("camoufox")
        with patch.dict(sys.modules, {"camoufox": camoufox, "camoufox.pkgman": pkgman, "camoufox.multiversion": multi}):
            _ensure_camoufox_active_install()
        multi.set_active.assert_called_once_with("browsers/official/152.0.4-beta.30-abc")

    def test_json_safe_camoufox_options_serializes_default_addons(self):
        import json
        from core.camoufox_driver import _json_safe_camoufox_options

        class _Addon:
            name = "UBO"

        payload = _json_safe_camoufox_options({
            "exclude_addons": [_Addon()],
            "proxy": {"server": "http://127.0.0.1:1", "username": "u"},
            "humanize": False,
        })
        self.assertEqual(payload["exclude_addons"], ["UBO"])
        self.assertNotIn("proxy", payload)
        json.dumps(payload)

    def test_create_camoufox_options_excludes_default_addons_like_grok_register(self):
        from core.camoufox_driver import create_camoufox_options
        with patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-addons"), \
             patch("core.camoufox_driver._excluded_default_addons", return_value=["UBO"]), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = True
            cfg.CAMOUFOX_GEOIP = False
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "en-US"
            cfg.CAMOUFOX_TIMEZONE = ""
            cfg.CAMOUFOX_USE_PROXY = False
            cfg.CAMOUFOX_OS = ""
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            opts = create_camoufox_options(proxy="")
        self.assertEqual(opts["exclude_addons"], ["UBO"])

    def test_docker_compose_disables_seccomp_for_firefox_tabs(self):
        """grok-register 用 seccomp=unconfined，否则 Firefox 内容进程会变成 Gah. Your tab just crashed。"""
        from pathlib import Path
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("seccomp=unconfined", compose)

    def test_adapt_camoufox_proxy_builds_playwright_dict(self):
        from core.camoufox_driver import adapt_camoufox_proxy
        self.assertEqual(
            adapt_camoufox_proxy("http://user:pass@127.0.0.1:7890"),
            {"server": "http://127.0.0.1:7890", "username": "user", "password": "pass"},
        )
        self.assertEqual(
            adapt_camoufox_proxy("socks5h://127.0.0.1:1080"),
            {"server": "socks5://127.0.0.1:1080"},
        )
        self.assertIsNone(adapt_camoufox_proxy(""))
        self.assertIsNone(adapt_camoufox_proxy(None))

    def test_create_camoufox_options_sets_fingerprint_and_temp_profile(self):
        from core.camoufox_driver import create_camoufox_options
        with patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-test"), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = True
            cfg.CAMOUFOX_GEOIP = True
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "ja-JP"
            cfg.CAMOUFOX_TIMEZONE = "Asia/Tokyo"
            cfg.CAMOUFOX_USE_PROXY = True
            cfg.CAMOUFOX_OS = "macos"
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            opts = create_camoufox_options(proxy="http://u:p@127.0.0.1:7890")
        self.assertFalse(opts["headless"])
        if "humanize" in opts:
            self.assertIn(opts["humanize"], (True, False))
        self.assertTrue(opts["geoip"])
        self.assertTrue(opts["block_webrtc"])
        self.assertTrue(opts["i_know_what_im_doing"])
        self.assertTrue(opts["persistent_context"])
        self.assertEqual(opts["user_data_dir"], "/tmp/turb-camoufox-test")
        self.assertEqual(opts["locale"], "ja-JP")
        self.assertEqual(opts["os"], "macos")
        self.assertEqual(opts["proxy"], {"server": "http://127.0.0.1:7890", "username": "u", "password": "p"})
        self.assertEqual(opts["config"]["timezone"], "Asia/Tokyo")

    def test_import_camoufox_missing_package_mentions_fetch(self):
        from core.camoufox_driver import import_camoufox
        camoufox_mod = types.ModuleType("camoufox")
        with patch.dict(sys.modules, {"camoufox": camoufox_mod, "camoufox.sync_api": None}):
            with self.assertRaises(RuntimeError) as ctx:
                import_camoufox()
        message = str(ctx.exception)
        self.assertIn("camoufox", message.lower())
        self.assertIn("fetch", message.lower())

    def test_build_camoufox_driver_wraps_playwright_adapter(self):
        from core.camoufox_driver import build_camoufox_driver

        class FakePage:
            def set_default_navigation_timeout(self, ms):
                self.nav_timeout = ms

            def set_default_timeout(self, ms):
                self.timeout = ms

        class FakeContext:
            def __init__(self):
                self.pages = []

            def new_page(self):
                page = FakePage()
                self.pages.append(page)
                return page

            def close(self):
                self.closed = True

        class FakeCamoufox:
            last_opts = None
            last_instance = None

            def __init__(self, **opts):
                FakeCamoufox.last_opts = opts
                FakeCamoufox.last_instance = self
                self.exited = False

            def __enter__(self):
                self.context = FakeContext()
                return self.context

            def __exit__(self, *args):
                self.exited = True

        with patch("core.camoufox_driver.import_camoufox", return_value=FakeCamoufox), \
             patch("core.camoufox_driver.create_camoufox_options", return_value={
                 "headless": False,
                 "persistent_context": True,
                 "user_data_dir": "/tmp/turb-camoufox-test",
             }), \
             patch("core.camoufox_driver._ensure_camoufox_active_install"), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            driver, opened = build_camoufox_driver(proxy="")
        self.assertIsInstance(driver, CloakSeleniumDriver)
        self.assertEqual(opened.profile_id, "camoufox")
        self.assertEqual(driver._registration_log_prefix, "[Camoufox注册]")
        driver.quit()
        self.assertTrue(FakeCamoufox.last_instance.exited)

    def test_build_camoufox_driver_forwards_http_proxy_auth_locally(self):
        from core.camoufox_driver import build_camoufox_driver

        class FakePage:
            def set_default_navigation_timeout(self, ms):
                self.nav_timeout = ms

            def set_default_timeout(self, ms):
                self.timeout = ms

        class FakeContext:
            def __init__(self):
                self.pages = []

            def new_page(self):
                page = FakePage()
                self.pages.append(page)
                return page

            def close(self):
                self.closed = True

        class FakeCamoufox:
            last_opts = None
            last_instance = None

            def __init__(self, **opts):
                FakeCamoufox.last_opts = opts
                FakeCamoufox.last_instance = self

            def __enter__(self):
                self.context = FakeContext()
                return self.context

            def __exit__(self, *args):
                self.exited = True

        with patch("core.camoufox_driver.import_camoufox", return_value=FakeCamoufox), \
             patch("core.camoufox_driver.tempfile.mkdtemp", return_value="/tmp/turb-camoufox-fwd"), \
             patch("core.camoufox_driver._infer_locale", return_value="en-US"), \
             patch("core.camoufox_driver._ensure_camoufox_active_install"), \
             patch("core.camoufox_driver._cfg") as cfg:
            cfg.CAMOUFOX_HEADLESS = False
            cfg.CAMOUFOX_HUMANIZE = False
            cfg.CAMOUFOX_GEOIP = False
            cfg.CAMOUFOX_BLOCK_WEBRTC = True
            cfg.CAMOUFOX_LOCALE = "en-US"
            cfg.CAMOUFOX_TIMEZONE = ""
            cfg.CAMOUFOX_USE_PROXY = True
            cfg.CAMOUFOX_OS = ""
            cfg.CAMOUFOX_USER_DATA_DIR = ""
            cfg.CAMOUFOX_SELENIUM_TIMEOUT = 90
            driver, opened = build_camoufox_driver(proxy="http://openai.9:secret@8.222.186.217:2260")
        try:
            proxy = FakeCamoufox.last_opts["proxy"]
            self.assertNotIn("username", proxy)
            self.assertNotIn("password", proxy)
            self.assertTrue(str(proxy["server"]).startswith("http://127.0.0.1:"))
            self.assertTrue(FakeCamoufox.last_opts["firefox_user_prefs"]["network.proxy.allow_hijacking_localhost"])
            self.assertIsNotNone(getattr(driver, "_proxy_forwarder", None))
            self.assertEqual(opened.raw["proxy"], "http://openai.9:secret@8.222.186.217:2260")
        finally:
            driver.quit()
        self.assertIsNone(getattr(driver, "_proxy_forwarder", None))

    def test_build_cloak_driver_dispatches_camoufox(self):
        sentinel = (object(), object())
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="camoufox"), \
             patch("core.camoufox_driver.build_camoufox_driver", return_value=sentinel) as mocked:
            from core.cloakbrowser_driver import build_cloak_driver
            result = build_cloak_driver(proxy="")
        mocked.assert_called_once_with(proxy="")
        self.assertIs(result, sentinel)

    def test_quit_exits_lifecycle_after_closing_context(self):
        class _Handle:
            def __init__(self):
                self.calls = []

            def close(self):
                self.calls.append("close")

            def __exit__(self, *args):
                self.calls.append("exit")

        context = _Handle()
        browser = _Handle()
        lifecycle = _Handle()
        driver = CloakSeleniumDriver(
            browser=browser, context=context, page=object(), lifecycle=lifecycle,
        )
        driver.quit()
        self.assertEqual(context.calls, ["close"])
        self.assertEqual(browser.calls, ["close"])
        self.assertEqual(lifecycle.calls, ["exit"])
        self.assertIsNone(driver._lifecycle)


if __name__ == "__main__":
    unittest.main()
