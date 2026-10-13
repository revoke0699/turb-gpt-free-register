# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from config import email as email_config
from core import email_provider


class OutlookEmailProviderTests(unittest.TestCase):
    def test_parse_sources_keeps_outlookemail(self):
        self.assertEqual(
            email_provider.parse_email_sources("outlook,outlookemail,remail,outlookemail"),
            ["outlook", "outlookemail", "remail"],
        )

    @patch("core.outlookemail_client.pick_account")
    def test_acquire_email_uses_outlookemail_client(self, pick_account):
        pick_account.return_value.email = "fresh@outlookemail.test"
        with patch("core.email_provider.parse_email_sources", return_value=["outlookemail"]):
            self.assertEqual(email_provider.acquire_email(), "fresh@outlookemail.test")

    @patch("core.outlookemail_client.get_account_context", return_value=object())
    def test_resolve_email_source_recognizes_cached_outlookemail_address(self, get_context):
        self.assertEqual(email_provider.resolve_email_source("fresh@outlookemail.test"), "outlookemail")
        get_context.assert_called_once_with("fresh@outlookemail.test")

    @patch("core.outlookemail_client.fetch_latest_otp", return_value="654321")
    @patch("core.email_provider.resolve_email_source", return_value="outlookemail")
    def test_wait_for_otp_uses_outlookemail_client(self, resolve, fetch_latest_otp):
        with patch.object(email_config, "USE_EMAIL_SERVICE", True):
            self.assertEqual(
                email_provider.wait_for_otp("fresh@outlookemail.test", after_ts=123.0),
                "654321",
            )
        fetch_latest_otp.assert_called_once_with("fresh@outlookemail.test", after_ts=123.0)

    @patch("core.outlookemail_client.release_account")
    @patch("core.email_provider.resolve_email_source", return_value="outlookemail")
    def test_release_email_uses_outlookemail_client(self, resolve, release_account):
        self.assertEqual(
            email_provider.release_email("fresh@outlookemail.test", status="available", note="中断"),
            "outlookemail",
        )
        release_account.assert_called_once_with("fresh@outlookemail.test", status="available", note="中断")


if __name__ == "__main__":
    unittest.main()
