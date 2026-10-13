# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from core.account_export import save_account_data


class OutlookEmailSaveHookTests(unittest.TestCase):
    def test_save_marks_mailbox_used_only_after_insert(self):
        order = []

        def insert_account(**kwargs):
            order.append("insert")
            self.assertEqual(kwargs["email_source"], "outlookemail")
            return 7

        def release_account(email, status, note):
            order.append("release")
            self.assertEqual(email, "alias@outlook.test")
            self.assertEqual(status, "used")
            self.assertEqual(note, "注册成功")

        with patch("core.db.insert_account", side_effect=insert_account), patch(
            "core.chatgpt2api_export.export_and_record_account",
            return_value={"status": "skipped", "ok": False, "message": "off"},
        ), patch("core.outlookemail_client.release_account", side_effect=release_account):
            row_id = save_account_data(
                email="alias@outlook.test",
                access_token="eyJ-access",
                email_source="outlookemail",
                auto_plan_check=False,
            )

        self.assertEqual(row_id, 7)
        self.assertEqual(order, ["insert", "release"])

    def test_remote_complete_failure_does_not_fail_save(self):
        with patch("core.db.insert_account", return_value=8), patch(
            "core.chatgpt2api_export.export_and_record_account",
            return_value={"status": "skipped", "ok": False, "message": "off"},
        ), patch("core.outlookemail_client.release_account", side_effect=RuntimeError("remote down")):
            row_id = save_account_data(
                email="alias@outlook.test",
                access_token="eyJ-access",
                email_source="outlookemail",
                auto_plan_check=False,
            )
        self.assertEqual(row_id, 8)

    def test_insert_failure_does_not_consume_mailbox(self):
        with patch("core.db.insert_account", side_effect=RuntimeError("db")), patch(
            "core.outlookemail_client.release_account"
        ) as release:
            with self.assertRaisesRegex(RuntimeError, "db"):
                save_account_data(
                    email="alias@outlook.test",
                    access_token="eyJ-access",
                    email_source="outlookemail",
                    auto_plan_check=False,
                )
        release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
