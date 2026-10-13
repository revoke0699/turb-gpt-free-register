# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from config import email as email_config
from webui.app import create_app


class OutlookEmailWebUiTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(auth_code="test-auth").test_client()
        self.client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"

    @patch("webui.app.svc.submit_registration")
    def test_jobs_rejects_outlookemail_without_password(self, submit_registration):
        with patch.object(email_config, "USE_EMAIL_SERVICE", True), patch.object(
            email_config, "EMAIL_SOURCE", "outlookemail"
        ), patch.object(email_config, "OUTLOOKEMAIL_API_BASE", "http://mail.local:5000", create=True), patch.object(
            email_config, "OUTLOOKEMAIL_PASSWORD", "", create=True
        ), patch.object(email_config, "OUTLOOKEMAIL_PROJECT_KEY", "gpt", create=True):
            response = self.client.post("/api/jobs", json={"count": 1, "workers": 1})

        self.assertEqual(response.status_code, 400)
        self.assertIn("outlookEmail 登录密码", response.get_json()["error"])
        submit_registration.assert_not_called()

    @patch("webui.app.db.outlook_pool_summary")
    @patch("webui.app.svc.submit_registration", return_value=[{"id": 1}])
    def test_jobs_with_outlookemail_config_does_not_check_local_pool(self, submit_registration, outlook_pool_summary):
        with patch.object(email_config, "USE_EMAIL_SERVICE", True), patch.object(
            email_config, "EMAIL_SOURCE", "outlookemail"
        ), patch.object(email_config, "OUTLOOKEMAIL_API_BASE", "http://mail.local:5000", create=True), patch.object(
            email_config, "OUTLOOKEMAIL_PASSWORD", "secret", create=True
        ), patch.object(email_config, "OUTLOOKEMAIL_PROJECT_KEY", "gpt", create=True):
            response = self.client.post("/api/jobs", json={"count": 1, "workers": 1})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["warning"], "")
        outlook_pool_summary.assert_not_called()
        submit_registration.assert_called_once_with(count=1, workers=1)

    @patch("webui.app.db.domain_email_pool_summary", return_value={"total": 0, "available": 0, "used": 0, "failed": 0})
    @patch("webui.app.db.outlook_pool_summary")
    @patch("webui.app.db.count_accounts", return_value=0)
    def test_summary_does_not_count_outlookemail_as_outlook_pool(self, count_accounts, outlook_pool_summary, domain_pool_summary):
        with patch.object(email_config, "EMAIL_SOURCE", "outlookemail"):
            response = self.client.get("/api/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["outlook_total"], 0)
        outlook_pool_summary.assert_not_called()

    def test_change_email_accepts_outlookemail_source(self):
        response = self.client.post("/api/accounts/1/change-email", json={"source": "outlookemail"})
        self.assertNotEqual(response.get_json().get("error"), "请选择有效的邮箱来源")


if __name__ == "__main__":
    unittest.main()
