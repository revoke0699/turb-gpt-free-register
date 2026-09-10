# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import db
from webui import config_editor
from webui.app import create_app


def _storage(root: Path) -> dict:
    missing = root / "missing.json"
    return {
        "_ACCOUNTS_JSON": root / "accounts.json",
        "_OUTLOOK_JSON": root / "outlook.json",
        "_GENERIC_API_EMAIL_JSON": root / "generic.json",
        "_DOMAIN_EMAIL_JSON": root / "domain.json",
        "_JOBS_JSON": root / "jobs.json",
        "_LEGACY_ACCOUNTS_JSON": root / "legacy-accounts.json",
        "_LEGACY_OUTLOOK_JSON": root / "legacy-outlook.json",
        "_LEGACY_JOBS_JSON": root / "legacy-jobs.json",
        "_LEGACY_SQLITE": root / "legacy.db",
        "_CODEX_DIR": root / "codex_accounts",
        "_CODEX_AGENT_DIR": root / "codex_agent_accounts",
        "_LEGACY_CODEX_EXPORT_STATE": root / "codex-export.json",
        "_SQLITE_READY": False,
        "_SQLITE_READY_PATH": None,
        "_VIEWER_HTML": root / "viewer.html",
        "_ACCOUNTS_TXT": root / "accounts.txt",
        "_TOKENS_TXT": root / "tokens.txt",
        "_LOG_DIR": root / "logs",
        "_LEGACY_OUTLOOK_JSON": missing,
    }


class ChatGPT2APIPayloadTests(unittest.TestCase):
    def test_build_payload_includes_token_email_password_proxy_and_plan(self):
        from core.chatgpt2api_export import build_chatgpt2api_account_payload

        payload = build_chatgpt2api_account_payload({
            "email": "user@example.com",
            "access_token": "eyJ-access",
            "proxy_used": "socks5://127.0.0.1:7897",
            "plan_type": "plus",
            "extra_json": json.dumps({"registration_password": "Aa1!Bb2@Cc3#"}),
        })
        self.assertEqual(payload["access_token"], "eyJ-access")
        self.assertEqual(payload["email"], "user@example.com")
        self.assertEqual(payload["password"], "Aa1!Bb2@Cc3#")
        self.assertEqual(payload["proxy"], "socks5://127.0.0.1:7897")
        self.assertEqual(payload["type"], "plus")
        self.assertEqual(payload["source_type"], "web")

    def test_build_payload_returns_none_without_access_token(self):
        from core.chatgpt2api_export import build_chatgpt2api_account_payload

        self.assertIsNone(build_chatgpt2api_account_payload({
            "email": "user@example.com",
            "access_token": "  ",
        }))

    def test_accounts_url_appends_path_and_does_not_double(self):
        from core.chatgpt2api_export import chatgpt2api_accounts_url

        self.assertEqual(
            chatgpt2api_accounts_url("http://127.0.0.1:3000/"),
            "http://127.0.0.1:3000/api/accounts",
        )
        self.assertEqual(
            chatgpt2api_accounts_url("http://127.0.0.1:3000/api/accounts"),
            "http://127.0.0.1:3000/api/accounts",
        )


class ChatGPT2APIExportTests(unittest.TestCase):
    def test_export_skips_when_auto_export_disabled(self):
        from core.chatgpt2api_export import export_account_to_chatgpt2api

        with patch("core.chatgpt2api_export._cfg") as cfg:
            cfg.CHATGPT2API_AUTO_EXPORT = False
            cfg.CHATGPT2API_API_BASE = "http://127.0.0.1:3000"
            cfg.CHATGPT2API_AUTH_KEY = "secret"
            result = export_account_to_chatgpt2api({
                "email": "user@example.com",
                "access_token": "eyJ-access",
            })
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["ok"])
        self.assertIn("AUTO_EXPORT", result["message"])

    def test_export_skips_when_base_empty(self):
        from core.chatgpt2api_export import export_account_to_chatgpt2api

        with patch("core.chatgpt2api_export._cfg") as cfg:
            cfg.CHATGPT2API_AUTO_EXPORT = True
            cfg.CHATGPT2API_API_BASE = ""
            cfg.CHATGPT2API_AUTH_KEY = "secret"
            result = export_account_to_chatgpt2api({
                "email": "user@example.com",
                "access_token": "eyJ-access",
            }, require_auto_export=False)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("API_BASE", result["message"])

    def test_export_posts_accounts_payload_with_bearer_auth(self):
        from core.chatgpt2api_export import export_account_to_chatgpt2api

        response = MagicMock()
        response.status_code = 200
        response.text = '{"added":1,"skipped":0}'
        response.json.return_value = {"added": 1, "skipped": 0, "refreshed": 1, "errors": []}

        with patch("core.chatgpt2api_export._cfg") as cfg, \
                patch("core.chatgpt2api_export.requests.post", return_value=response) as posted:
            cfg.CHATGPT2API_AUTO_EXPORT = True
            cfg.CHATGPT2API_API_BASE = "http://127.0.0.1:3000"
            cfg.CHATGPT2API_AUTH_KEY = "admin-key"
            cfg.CHATGPT2API_API_TIMEOUT = 15
            cfg.CHATGPT2API_SOURCE_TYPE = "web"
            result = export_account_to_chatgpt2api({
                "email": "user@example.com",
                "access_token": "eyJ-access",
                "extra_json": json.dumps({"registration_password": "Pw#12345"}),
                "proxy_used": "http://proxy.local:8080",
                "plan_type": "free",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["added"], 1)
        posted.assert_called_once()
        args, kwargs = posted.call_args
        self.assertEqual(args[0], "http://127.0.0.1:3000/api/accounts")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer admin-key")
        self.assertEqual(kwargs["json"]["accounts"][0]["access_token"], "eyJ-access")
        self.assertEqual(kwargs["json"]["accounts"][0]["password"], "Pw#12345")
        self.assertEqual(kwargs["json"]["accounts"][0]["proxy"], "http://proxy.local:8080")
        self.assertEqual(kwargs["timeout"], 15)

    def test_export_http_error_does_not_raise(self):
        from core.chatgpt2api_export import export_account_to_chatgpt2api

        response = MagicMock()
        response.status_code = 401
        response.text = '{"error":"密钥无效"}'
        response.json.return_value = {"error": "密钥无效"}

        with patch("core.chatgpt2api_export._cfg") as cfg, \
                patch("core.chatgpt2api_export.requests.post", return_value=response):
            cfg.CHATGPT2API_AUTO_EXPORT = True
            cfg.CHATGPT2API_API_BASE = "http://127.0.0.1:3000"
            cfg.CHATGPT2API_AUTH_KEY = "bad"
            cfg.CHATGPT2API_API_TIMEOUT = 10
            cfg.CHATGPT2API_SOURCE_TYPE = "web"
            result = export_account_to_chatgpt2api({
                "email": "user@example.com",
                "access_token": "eyJ-access",
            })
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["http_status"], 401)


class ChatGPT2APISaveHookTests(unittest.TestCase):
    def test_save_account_data_auto_exports_and_records_status(self):
        from core.account_export import save_account_data

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **_storage(root)), \
                    patch("core.chatgpt2api_export.export_account_to_chatgpt2api") as mocked:
                mocked.return_value = {
                    "status": "success",
                    "ok": True,
                    "http_status": 200,
                    "added": 1,
                    "skipped": 0,
                    "message": "ok",
                }
                row_id = save_account_data(
                    email="user@example.com",
                    access_token="eyJ-access",
                    extra={"registration_password": "Pw#12345", "account": {"planType": "free"}},
                    proxy_used="http://proxy.local:8080",
                    auto_plan_check=False,
                )
                mocked.assert_called_once()
                sent = mocked.call_args.args[0]
                self.assertEqual(sent["email"], "user@example.com")
                self.assertEqual(sent["access_token"], "eyJ-access")
                row = db.get_account(row_id)
                self.assertEqual(row.get("chatgpt2api_status"), "success")

    def test_save_account_data_keeps_account_when_export_fails(self):
        from core.account_export import save_account_data

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.multiple(db, **_storage(root)), \
                    patch("core.chatgpt2api_export.export_account_to_chatgpt2api", side_effect=RuntimeError("boom")):
                row_id = save_account_data(
                    email="keep@example.com",
                    access_token="eyJ-keep",
                    auto_plan_check=False,
                )
                row = db.get_account(row_id)
                self.assertEqual(row["email"], "keep@example.com")
                self.assertEqual(row["access_token"], "eyJ-keep")
                self.assertEqual(row.get("chatgpt2api_status"), "failed")


class ChatGPT2APIConfigTests(unittest.TestCase):
    def test_webui_exposes_chatgpt2api_settings(self):
        fields = {field["key"]: field for field in config_editor.EDITABLE_FIELDS}
        self.assertEqual(fields["CHATGPT2API_AUTO_EXPORT"]["type"], "bool")
        self.assertEqual(fields["CHATGPT2API_API_BASE"]["type"], "str")
        self.assertEqual(fields["CHATGPT2API_AUTH_KEY"]["type"], "str")
        self.assertTrue(fields["CHATGPT2API_AUTH_KEY"].get("secret"))
        self.assertEqual(fields["CHATGPT2API_AUTH_KEY"].get("storage"), "env")
        self.assertEqual(fields["CHATGPT2API_API_TIMEOUT"]["type"], "int")


class ChatGPT2APIWebUiTests(unittest.TestCase):
    def test_single_and_bulk_upload_endpoints(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "accounts.json").write_text(json.dumps([
                {"id": 1, "email": "a@example.com", "access_token": "token-a"},
                {"id": 2, "email": "b@example.com"},
                {"id": 3, "email": "c@example.com", "access_token": "token-c"},
            ]), encoding="utf-8")
            with patch.multiple(db, **_storage(root)):
                app = create_app(auth_code="test-auth")
                client = app.test_client()

                def fake_export(row, **kwargs):
                    token = str((row or {}).get("access_token") or "")
                    if not token:
                        return {"status": "skipped", "ok": False, "message": "access_token 为空"}
                    return {"status": "success", "ok": True, "added": 1, "url": "http://c2a/api/accounts"}

                with patch("core.chatgpt2api_export.export_account_to_chatgpt2api", side_effect=fake_export):
                    one = client.post(
                        "/api/accounts/1/chatgpt2api/upload",
                        headers={"X-Auth-Code": "test-auth"},
                    )
                    bulk = client.post(
                        "/api/accounts/chatgpt2api/upload-bulk",
                        json={"account_ids": [1, 2, 3, 99, "bad"]},
                        headers={"X-Auth-Code": "test-auth"},
                    )

        self.assertEqual(one.status_code, 200)
        self.assertTrue(one.get_json()["ok"])
        body = bulk.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["uploaded_count"], 2)
        self.assertEqual({item["id"] for item in body["uploaded"]}, {1, 3})
        self.assertEqual(
            {(item["id"], item["reason"]) for item in body["skipped"]},
            {(2, "缺少 access_token"), (99, "账号不存在"), ("bad", "ID 非法")},
        )


if __name__ == "__main__":
    unittest.main()
