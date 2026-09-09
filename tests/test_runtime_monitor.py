# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class RuntimeMonitorTests(unittest.TestCase):
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

    def test_create_job_stores_batch_id(self):
        job = db.create_job("outlook", batch_id="web-test-1")
        self.assertEqual(job["batch_id"], "web-test-1")
        loaded = db.get_job(job["id"])
        self.assertEqual(loaded["batch_id"], "web-test-1")
        self.assertEqual([row["id"] for row in db.list_jobs_by_batch("web-test-1")], [job["id"]])

    def test_submit_registration_assigns_shared_batch_id(self):
        executor = MagicMock()
        with patch.object(svc, "get_executor", return_value=executor), patch.object(
            svc, "get_executor_workers", return_value=2
        ):
            jobs = svc.submit_registration(count=3, workers=2)

        self.assertEqual(len(jobs), 3)
        batch_ids = {job.get("batch_id") for job in jobs}
        self.assertEqual(len(batch_ids), 1)
        batch_id = jobs[0]["batch_id"]
        self.assertTrue(str(batch_id).startswith("web-"))
        self.assertEqual(executor.submit.call_count, 3)
        current = db.get_current_batch()
        self.assertEqual(current.get("batch_id"), batch_id)
        self.assertEqual(int(current.get("target_count") or 0), 3)
        self.assertEqual(int(current.get("workers") or 0), 2)

    def test_runtime_snapshot_aggregates_current_batch_only(self):
        batch_id = "web-test-agg"
        other = db.create_job("outlook", batch_id="web-old")
        db.update_job(other["id"], status="failed", error="旧批次")
        first = db.create_job("outlook", batch_id=batch_id)
        second = db.create_job("outlook", batch_id=batch_id)
        db.update_job(
            first["id"],
            status="success",
            email="ok@example.com",
            started_at="2026-09-09T10:00:00",
            completed_at="2026-09-09T10:01:00",
        )
        db.update_job(
            second["id"],
            status="running",
            email="run@example.com",
            started_at="2026-09-09T10:00:30",
        )
        svc.begin_runtime_batch(batch_id, target_count=2, workers=3, started_at="2026-09-09T10:00:00")
        snap = svc.get_runtime_snapshot()
        self.assertEqual(snap["batch_id"], batch_id)
        self.assertTrue(snap["running"])
        self.assertEqual(snap["target_count"], 2)
        self.assertEqual(snap["workers"], 3)
        self.assertEqual(snap["success_count"], 1)
        self.assertEqual(snap["failure_count"], 0)
        self.assertEqual(snap["completed_count"], 1)
        self.assertEqual(snap["pending_count"], 1)
        self.assertEqual(snap["progress_percent"], 50)
        self.assertIn("run@example.com", snap["current_emails"])
        self.assertNotIn("旧批次", str(snap.get("last_error") or ""))

    def test_runtime_logs_are_incremental(self):
        svc.begin_runtime_batch("web-logs", target_count=1, workers=1)
        job = db.create_job("outlook", batch_id="web-logs")
        svc.append_runtime_log(job["id"], "INFO", "开始注册")
        svc.append_runtime_log(job["id"], "ERROR", "验证码超时")
        first = svc.get_runtime_snapshot()
        self.assertEqual(len(first["logs"]), 2)
        self.assertEqual(first["logs"][0]["message"], "开始注册")
        self.assertEqual(first["logs"][1]["level"], "ERROR")
        later = svc.get_runtime_snapshot(after_id=first["latest_log_id"])
        self.assertEqual(later["logs"], [])
        svc.append_runtime_log(job["id"], "INFO", "重试中")
        delta = svc.get_runtime_snapshot(after_id=first["latest_log_id"])
        self.assertEqual([item["message"] for item in delta["logs"]], ["重试中"])

    def test_stop_batch_cancels_pending_jobs(self):
        batch_id = "web-stop"
        pending = db.create_job("outlook", batch_id=batch_id)
        done = db.create_job("outlook", batch_id=batch_id)
        db.update_job(done["id"], status="success")
        svc.begin_runtime_batch(batch_id, target_count=2, workers=1)
        result = svc.request_stop_batch(batch_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["stopped_count"], 1)
        self.assertEqual(db.get_job(pending["id"])["status"], "cancelled")
        self.assertEqual(db.get_job(done["id"])["status"], "success")

    def test_retry_job_starts_new_current_batch(self):
        source = db.create_job("outlook", batch_id="web-old")
        db.update_job(source["id"], status="failed", error="timeout")
        executor = MagicMock()
        with patch.object(svc, "get_executor", return_value=executor), patch.object(
            svc, "get_executor_workers", return_value=1
        ):
            result = svc.retry_job(source["id"], workers=1)
        self.assertTrue(result["ok"])
        new_job = result["job"]
        self.assertTrue(str(new_job.get("batch_id") or "").startswith("web-"))
        self.assertNotEqual(new_job.get("batch_id"), "web-old")
        self.assertEqual(db.get_current_batch().get("batch_id"), new_job.get("batch_id"))


class RuntimeMonitorApiTests(unittest.TestCase):
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

    def test_api_runtime_returns_empty_snapshot_when_idle(self):
        response = self.client.get("/api/runtime")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["running"])
        self.assertEqual(payload["target_count"], 0)
        self.assertEqual(payload["logs"], [])

    def test_api_jobs_create_returns_batch_id(self):
        executor = MagicMock()
        with patch.object(svc, "get_executor", return_value=executor), patch.object(
            svc, "get_executor_workers", return_value=1
        ), patch("webui.app.svc.submit_registration", wraps=svc.submit_registration), patch(
            "config.email.USE_EMAIL_SERVICE", True
        ), patch(
            "config.email.EMAIL_SOURCE", "gptmail"
        ), patch(
            "config.email.GPTMAIL_API_KEY", "key-123"
        ):
            response = self.client.post(
                "/api/jobs",
                json={"count": 2, "workers": 1},
                headers={"Accept-Encoding": "identity", "X-Auth-Code": "test-auth"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["submitted"], 2)
        self.assertTrue(payload.get("batch_id"))
        self.assertEqual(payload["jobs"][0]["batch_id"], payload["batch_id"])

    def test_api_stop_batch_stops_current_round(self):
        job = db.create_job("outlook", batch_id="web-api-stop")
        svc.begin_runtime_batch("web-api-stop", target_count=1, workers=1)
        response = self.client.post("/api/jobs/stop-batch", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stopped_count"], 1)
        self.assertEqual(db.get_job(job["id"])["status"], "cancelled")


class RuntimeMonitorUiTests(unittest.TestCase):
    def test_modern_ui_has_runtime_tab(self):
        html = Path(__file__).resolve().parents[1].joinpath("webui/templates/index.html").read_text(encoding="utf-8")
        self.assertIn('id="tab-runtime"', html)
        self.assertIn('data-tab="runtime"', html)
        self.assertIn("/api/runtime", html)
        self.assertIn("停止本轮", html)
        self.assertIn("实时日志", html)


if __name__ == "__main__":
    unittest.main()
