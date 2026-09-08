# -*- coding: utf-8 -*-
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloakbrowser_driver import (
    _cloak_supports_http_proxy_inline_auth,
    import_stealth_browser,
    resolve_stealth_engine,
    stealth_log_tag,
)
from webui import config_editor


class ChromixDriverTests(unittest.TestCase):
    def test_chromix_registration_driver_selects_chromix_engine(self):
        with patch("config.roxybrowser.REGISTRATION_DRIVER", "chromix"):
            self.assertEqual(resolve_stealth_engine(), "chromix")
            self.assertEqual(stealth_log_tag(), "Chromix")

    def test_cloak_registration_driver_keeps_cloakbrowser_engine(self):
        with patch("config.roxybrowser.REGISTRATION_DRIVER", "cloak"):
            self.assertEqual(resolve_stealth_engine(), "cloakbrowser")
            self.assertEqual(stealth_log_tag(), "Cloak")

    def test_chromix_assumes_native_http_proxy_auth(self):
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="chromix"):
            self.assertTrue(_cloak_supports_http_proxy_inline_auth())

    def test_import_stealth_browser_loads_chromix_package(self):
        fake = types.ModuleType("chromix")
        fake.launch = object()
        fake.launch_persistent_context = object()
        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="chromix"), \
             patch.dict(sys.modules, {"chromix": fake}):
            launch, persist, name = import_stealth_browser()
        self.assertIs(launch, fake.launch)
        self.assertIs(persist, fake.launch_persistent_context)
        self.assertEqual(name, "chromix")

    def test_main_routes_chromix_to_cloak_registration(self):
        src = Path("main.py").read_text(encoding="utf-8")
        self.assertIn('"chromix"', src)
        self.assertIn("run_cloak_registration", src)

    def test_webui_exposes_chromix_driver_choice(self):
        help_text = next(f["help"] for f in config_editor.EDITABLE_FIELDS if f["key"] == "REGISTRATION_DRIVER")
        self.assertIn("chromix", help_text.lower())
        html = Path("webui/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("value: 'chromix'", html)


if __name__ == "__main__":
    unittest.main()
