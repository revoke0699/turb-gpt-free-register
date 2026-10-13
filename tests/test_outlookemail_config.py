# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from config import email
from config.env_loader import SECRET_ENV_KEYS
from webui.config_editor import EDITABLE_FIELDS


class OutlookEmailConfigTests(unittest.TestCase):
    def test_email_config_declares_outlookemail_defaults(self):
        source = Path(email.__file__).read_text(encoding="utf-8")
        self.assertIn('OUTLOOKEMAIL_API_BASE = env_str("OUTLOOKEMAIL_API_BASE", "")', source)
        self.assertIn('OUTLOOKEMAIL_PASSWORD = env_str("OUTLOOKEMAIL_PASSWORD", "")', source)
        self.assertIn('OUTLOOKEMAIL_PROJECT_KEY = "gpt"', source)
        self.assertIn("OUTLOOKEMAIL_LEASE_SECONDS = 1800", source)
        self.assertIn('"outlookemail"', source)

    def test_secret_registry_includes_outlookemail_password(self):
        self.assertEqual(SECRET_ENV_KEYS["OUTLOOKEMAIL_PASSWORD"], "outlookEmail 自建服务 Web 登录密码")

    def test_webui_exposes_outlookemail_fields(self):
        base_field = next(item for item in EDITABLE_FIELDS if item["key"] == "OUTLOOKEMAIL_API_BASE")
        self.assertEqual(base_field["group"], "邮箱 / OTP")
        self.assertEqual(base_field["storage"], "env")
        password_field = next(item for item in EDITABLE_FIELDS if item["key"] == "OUTLOOKEMAIL_PASSWORD")
        self.assertTrue(password_field["secret"])
        self.assertEqual(password_field["storage"], "env")
        project_field = next(item for item in EDITABLE_FIELDS if item["key"] == "OUTLOOKEMAIL_PROJECT_KEY")
        self.assertEqual(project_field["type"], "str")
        lease_field = next(item for item in EDITABLE_FIELDS if item["key"] == "OUTLOOKEMAIL_LEASE_SECONDS")
        self.assertEqual(lease_field["type"], "int")


if __name__ == "__main__":
    unittest.main()
