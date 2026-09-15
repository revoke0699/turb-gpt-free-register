# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

from core import mailnest_client


class MailNestClientTests(unittest.TestCase):
    def setUp(self):
        mailnest_client._CONTEXT_CACHE.clear()

    def test_pick_account_requires_api_key(self):
        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "", create=True):
            with self.assertRaisesRegex(mailnest_client.MailNestClientError, "MailNest API Key"):
                mailnest_client.pick_account()

    @patch("core.mailnest_client.requests.request")
    @patch("core.mailnest_client.requests.Session")
    def test_pick_account_uses_direct_connection(self, session_cls, request):
        """领取邮箱不能走环境变量里的 SOCKS/HTTP 代理，否则容器内 127.0.0.1 代理会 Connection refused。"""
        session = session_cls.return_value
        response = Mock(status_code=200)
        response.json.return_value = {"code": "00000", "data": [{"email": "fresh@mailnest.test"}]}
        session.request.return_value = response
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True), patch.object(
            mailnest_client._email_cfg, "MAIL_NEST_PROJECT_CODE", "chatgpt001", create=True
        ):
            account = mailnest_client.pick_account()

        self.assertEqual(account.email, "fresh@mailnest.test")
        self.assertFalse(session.trust_env)
        session.request.assert_called_once()
        request.assert_not_called()
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs.get("proxies"), {"http": None, "https": None})

    @patch("core.mailnest_client.requests.Session")
    def test_pick_account_buys_mailbox_with_project_code(self, session_cls):
        session = session_cls.return_value
        response = Mock(status_code=200)
        response.json.return_value = {"code": "00000", "data": [{"email": "fresh@mailnest.test"}]}
        session.request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True), patch.object(
            mailnest_client._email_cfg, "MAIL_NEST_PROJECT_CODE", "chatgpt001", create=True
        ):
            account = mailnest_client.pick_account()

        self.assertEqual(account.email, "fresh@mailnest.test")
        self.assertIs(mailnest_client.get_account_context("fresh@mailnest.test"), account)
        session.request.assert_called_once()
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer key-123")
        self.assertEqual(kwargs["json"], {"project_code": "chatgpt001", "count": 1})

    @patch("core.mailnest_client.time.sleep")
    @patch("core.mailnest_client.requests.Session")
    def test_fetch_latest_otp_reads_code_match(self, session_cls, sleep):
        session = session_cls.return_value
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": [{"code_match": "654321", "subject": "OpenAI code", "from": "noreply@openai.com", "timestamp": 205}],
        }
        session.request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            code = mailnest_client.fetch_latest_otp(
                "fresh@mailnest.test",
                after_ts=200,
                max_wait=1,
                poll_interval=1,
                settle_seconds=0,
            )

        self.assertEqual(code, "654321")


if __name__ == "__main__":
    unittest.main()
