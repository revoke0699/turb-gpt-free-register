# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from core import outlookemail_client


class _Response:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.trust_env = True
        self.calls = []
        self.emails = [{
            "id": "m1",
            "subject": "Your OpenAI code",
            "from": "noreply@openai.com",
            "date": "2026-04-15T10:05:00+00:00",
            "body_preview": "Your code is 654321",
            "folder": "inbox",
        }]

    def request(self, method, url, params=None, json=None, headers=None, timeout=None, proxies=None):
        self.calls.append({
            "method": method,
            "url": url,
            "params": params,
            "json": json,
            "headers": headers or {},
        })
        if url.endswith("/login"):
            if (json or {}).get("password") != "secret":
                return _Response({"success": False, "error": "密码错误"})
            return _Response({"success": True, "message": "登录成功"})
        if url.endswith("/api/csrf-token"):
            return _Response({"csrf_token": "csrf-1", "csrf_disabled": False})
        if url.endswith("/claim-random"):
            return _Response({
                "success": True,
                "data": {
                    "account_id": 12,
                    "email": "alias@outlook.test",
                    "claim_token": "pclm_xxx",
                    "project_key": "gpt",
                },
            })
        if "/complete-success" in url:
            return _Response({"success": True, "message": "项目账号已标记成功"})
        if "/complete-failed" in url:
            return _Response({"success": True, "message": "项目账号已标记失败"})
        if url.endswith("/release"):
            return _Response({"success": True, "message": "项目账号已释放"})
        if "/api/emails/" in url:
            return _Response({"success": True, "emails": self.emails})
        raise AssertionError(url)


class OutlookEmailClientTests(unittest.TestCase):
    def setUp(self):
        outlookemail_client._CONTEXT_CACHE.clear()
        outlookemail_client._SESSION = None
        self.session = _Session()

    def _configured(self):
        return patch.multiple(
            outlookemail_client._email_cfg,
            OUTLOOKEMAIL_API_BASE="http://mail.local:5000",
            OUTLOOKEMAIL_PASSWORD="secret",
            OUTLOOKEMAIL_PROJECT_KEY="gpt",
            OUTLOOKEMAIL_LEASE_SECONDS=1800,
            create=True,
        )

    def test_missing_base_is_rejected(self):
        with patch.object(outlookemail_client._email_cfg, "OUTLOOKEMAIL_API_BASE", "", create=True), patch.object(
            outlookemail_client._email_cfg, "OUTLOOKEMAIL_PASSWORD", "secret", create=True
        ):
            with self.assertRaisesRegex(outlookemail_client.OutlookEmailError, "API 地址"):
                outlookemail_client.pick_account()

    def test_missing_password_is_rejected(self):
        with patch.object(outlookemail_client._email_cfg, "OUTLOOKEMAIL_API_BASE", "http://mail.local:5000", create=True), patch.object(
            outlookemail_client._email_cfg, "OUTLOOKEMAIL_PASSWORD", "", create=True
        ):
            with self.assertRaisesRegex(outlookemail_client.OutlookEmailError, "登录密码"):
                outlookemail_client.pick_account()

    @patch("core.outlookemail_client._new_session")
    def test_pick_claims_project_mailbox(self, new_session):
        new_session.return_value = self.session
        with self._configured():
            account = outlookemail_client.pick_account()
        self.assertEqual(account.email, "alias@outlook.test")
        self.assertEqual(account.account_id, 12)
        self.assertEqual(account.claim_token, "pclm_xxx")
        self.assertFalse(self.session.trust_env)
        claim = next(item for item in self.session.calls if item["url"].endswith("/claim-random"))
        self.assertEqual(claim["headers"]["X-CSRFToken"], "csrf-1")
        self.assertEqual(claim["json"]["caller_id"], "turb-gpt-free-register")
        self.assertEqual(claim["json"]["lease_seconds"], 1800)
        self.assertTrue(claim["json"]["task_id"])
        self.assertIs(outlookemail_client.get_account_context("alias@outlook.test"), account)

    @patch("core.outlookemail_client._new_session")
    def test_wrong_password_is_reported(self, new_session):
        new_session.return_value = self.session
        with patch.object(outlookemail_client._email_cfg, "OUTLOOKEMAIL_API_BASE", "http://mail.local:5000", create=True), patch.object(
            outlookemail_client._email_cfg, "OUTLOOKEMAIL_PASSWORD", "nope", create=True
        ), patch.object(outlookemail_client._email_cfg, "OUTLOOKEMAIL_PROJECT_KEY", "gpt", create=True):
            with self.assertRaisesRegex(outlookemail_client.OutlookEmailError, "密码错误"):
                outlookemail_client.pick_account()

    @patch("core.outlookemail_client.time.sleep")
    @patch("core.outlookemail_client._new_session")
    def test_fetch_otp_from_preview(self, new_session, _sleep):
        new_session.return_value = self.session
        with self._configured():
            outlookemail_client.pick_account()
            code = outlookemail_client.fetch_latest_otp(
                "alias@outlook.test",
                after_ts=1,
                max_wait=1,
                poll_interval=1,
                settle_seconds=0,
            )
        self.assertEqual(code, "654321")

    @patch("core.outlookemail_client._new_session")
    def test_release_maps_status_to_project_api(self, new_session):
        new_session.return_value = self.session
        with self._configured():
            outlookemail_client.pick_account()
            outlookemail_client.release_account("alias@outlook.test", status="available", note="中断")
        release = next(item for item in self.session.calls if item["url"].endswith("/release"))
        self.assertEqual(release["json"]["account_id"], 12)
        self.assertEqual(release["json"]["claim_token"], "pclm_xxx")
        self.assertIsNone(outlookemail_client.get_account_context("alias@outlook.test"))

        self.session.calls.clear()
        with self._configured():
            outlookemail_client.pick_account()
            outlookemail_client.release_account("alias@outlook.test", status="failed", note="创建后失败")
        failed = next(item for item in self.session.calls if "/complete-failed" in item["url"])
        self.assertEqual(failed["json"]["claim_token"], "pclm_xxx")

        self.session.calls.clear()
        with self._configured():
            outlookemail_client.pick_account()
            outlookemail_client.release_account("alias@outlook.test", status="used", note="注册成功")
        done = next(item for item in self.session.calls if "/complete-success" in item["url"])
        self.assertEqual(done["json"]["account_id"], 12)


if __name__ == "__main__":
    unittest.main()
