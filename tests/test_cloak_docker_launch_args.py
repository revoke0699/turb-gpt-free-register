# -*- coding: utf-8 -*-
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.cloakbrowser_driver import merge_cloak_launch_args, running_in_docker


class CloakDockerLaunchArgsTests(unittest.TestCase):
    def test_host_launch_args_keep_user_flags_only(self):
        self.assertEqual(
            merge_cloak_launch_args(["--fingerprint=12345"], in_docker=False),
            ["--fingerprint=12345"],
        )

    def test_docker_launch_args_add_sandbox_and_shm_flags(self):
        self.assertEqual(
            merge_cloak_launch_args(["--fingerprint=12345"], in_docker=True),
            ["--fingerprint=12345", "--no-sandbox", "--disable-dev-shm-usage"],
        )

    def test_docker_launch_args_do_not_duplicate_existing_flags(self):
        self.assertEqual(
            merge_cloak_launch_args(
                ["--no-sandbox", "--disable-dev-shm-usage"],
                in_docker=True,
            ),
            ["--no-sandbox", "--disable-dev-shm-usage"],
        )

    def test_running_in_docker_respects_explicit_env(self):
        with patch.dict(os.environ, {"TURB_IN_DOCKER": "1"}, clear=False):
            self.assertTrue(running_in_docker())
        with patch.dict(os.environ, {"TURB_IN_DOCKER": "0"}, clear=False):
            self.assertFalse(running_in_docker())

    def test_running_in_docker_detects_dockerenv_file(self):
        with patch.dict(os.environ, {"TURB_IN_DOCKER": ""}, clear=False):
            with patch.object(Path, "exists", return_value=True):
                self.assertTrue(running_in_docker())
            with patch.object(Path, "exists", return_value=False):
                self.assertFalse(running_in_docker())

    def test_build_cloak_driver_passes_docker_flags_to_launch(self):
        launch = MagicMock(name="launch")
        persist = MagicMock(name="persist")
        browser = MagicMock(name="browser")
        context = MagicMock(name="context")
        page = MagicMock(name="page")
        browser.new_context.return_value = context
        context.new_page.return_value = page
        launch.return_value = browser

        with patch("core.cloakbrowser_driver.resolve_stealth_engine", return_value="cloakbrowser"), \
             patch("core.cloakbrowser_driver.import_stealth_browser", return_value=(launch, persist, "cloakbrowser")), \
             patch("core.cloakbrowser_driver.running_in_docker", return_value=True), \
             patch("core.cloakbrowser_driver._build_cloak_locale_options", return_value={}), \
             patch.object(_cfg_mod(), "CLOAK_EXTRA_ARGS", []), \
             patch.object(_cfg_mod(), "CLOAK_USE_PROXY", False), \
             patch.object(_cfg_mod(), "CLOAK_USER_DATA_DIR", ""), \
             patch.object(_cfg_mod(), "CLOAK_FINGERPRINT_SEED", ""), \
             patch.object(_cfg_mod(), "CLOAK_LICENSE_KEY", ""), \
             patch.object(_cfg_mod(), "CLOAK_HEADLESS", False), \
             patch.object(_cfg_mod(), "CLOAK_HUMANIZE", False), \
             patch.object(_cfg_mod(), "CLOAK_GEOIP", False):
            from core.cloakbrowser_driver import build_cloak_driver
            build_cloak_driver(proxy="")

        args = launch.call_args.kwargs.get("args") or []
        self.assertFalse(launch.call_args.kwargs.get("headless"))
        self.assertIn("--no-sandbox", args)
        self.assertIn("--disable-dev-shm-usage", args)
        persist.assert_not_called()


def _cfg_mod():
    from config import cloakbrowser as cfg
    return cfg


if __name__ == "__main__":
    unittest.main()
