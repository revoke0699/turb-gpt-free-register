# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import db
from core import registration_service as svc
from webui.app import create_app


def _storage(root: Path) -> dict:
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
        "_CODEX_DIR": root / "codex",
        "_CODEX_AGENT_DIR": root / "agent",
        "_LEGACY_CODEX_EXPORT_STATE": root / "state.json",
        "_LOG_DIR": root / "logs",
        "_SQLITE_READY": False,
        "_SQLITE_READY_PATH": None,
    }


class JobsListPageTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self._db_patch = patch.multiple(db, **_storage(root))
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        self.addCleanup(self.td.cleanup)

    def test_list_jobs_page_excludes_pending(self):
        pending = db.create_job("outlook", batch_id="b1")
        running = db.create_job("outlook", batch_id="b1")
        done = db.create_job("outlook", batch_id="b1")
        db.update_job(running["id"], status="running", email="run@example.com")
        db.update_job(done["id"], status="success", email="ok@example.com")

        result = db.list_jobs_page(limit=50, offset=0)
        ids = [int(row["id"]) for row in result["items"]]
        self.assertNotIn(int(pending["id"]), ids)
        self.assertIn(int(running["id"]), ids)
        self.assertIn(int(done["id"]), ids)
        self.assertEqual(result["total"], 2)

    def test_list_jobs_page_caps_to_recent_window(self):
        ids = []
        for i in range(60):
            job = db.create_job("outlook", batch_id="old")
            db.update_job(job["id"], status="success", email=f"ok{i}@example.com")
            ids.append(int(job["id"]))
        newest = ids[-1]
        oldest = ids[0]

        result = db.list_jobs_page(limit=50, offset=0)
        returned = [int(row["id"]) for row in result["items"]]
        self.assertEqual(result["total"], 50)
        self.assertEqual(len(returned), 50)
        self.assertIn(newest, returned)
        self.assertNotIn(oldest, returned)


class JobsListApiTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self._db_patch = patch.multiple(db, **_storage(root))
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        self.addCleanup(self.td.cleanup)
        reset = getattr(svc, "reset_runtime_state", None)
        if callable(reset):
            reset()
        self.client = create_app(auth_code="test-auth").test_client()
        self.client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"

    def test_api_jobs_paged_hides_pending_and_caps_history(self):
        pending = db.create_job("outlook", batch_id="api")
        for i in range(55):
            job = db.create_job("outlook", batch_id="api")
            db.update_job(job["id"], status="failed", email=f"fail{i}@example.com")
        running = db.create_job("outlook", batch_id="api")
        db.update_job(running["id"], status="running", email="run@example.com")

        response = self.client.get(
            "/api/jobs?paged=1&page=1&page_size=50",
            headers={"Accept-Encoding": "identity", "X-Auth-Code": "test-auth"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        ids = [int(row["id"]) for row in payload["items"]]
        self.assertNotIn(int(pending["id"]), ids)
        self.assertIn(int(running["id"]), ids)
        self.assertLessEqual(payload["total"], 50)
        self.assertLessEqual(len(payload["items"]), 50)
        self.assertTrue(all(row.get("status") != "pending" for row in payload["items"]))

    def test_runtime_snapshot_jobs_exclude_pending(self):
        batch_id = "web-hide-pending"
        pending = db.create_job("outlook", batch_id=batch_id)
        running = db.create_job("outlook", batch_id=batch_id)
        db.update_job(running["id"], status="running", email="run@example.com")
        svc.begin_runtime_batch(batch_id, target_count=2, workers=1)
        snap = svc.get_runtime_snapshot()
        ids = [int(row["id"]) for row in snap.get("jobs") or []]
        self.assertNotIn(int(pending["id"]), ids)
        self.assertIn(int(running["id"]), ids)
        self.assertEqual(snap["pending_count"], 2)

        response = self.client.get(
            "/api/runtime",
            headers={"Accept-Encoding": "identity", "X-Auth-Code": "test-auth"},
        )
        payload = response.get_json()
        api_ids = [int(row["id"]) for row in payload.get("jobs") or []]
        self.assertNotIn(int(pending["id"]), api_ids)
        self.assertEqual(payload["pending_count"], 2)


class JobsListUiTests(unittest.TestCase):
    def test_modern_ui_says_recent_jobs_only(self):
        html = Path(__file__).resolve().parents[1].joinpath("webui/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("不含排队", html)


if __name__ == "__main__":
    unittest.main()
