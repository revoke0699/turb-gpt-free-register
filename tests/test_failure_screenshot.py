# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import db
from core import failure_screenshot
from core import registration_service as svc
from webui.app import create_app

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _storage(root: Path) -> dict:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
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
        "_LOG_DIR": log_dir,
        "_SQLITE_READY": False,
        "_SQLITE_READY_PATH": None,
    }


class FailureScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self._db_patch = patch.multiple(db, **_storage(self.root))
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        self.addCleanup(self.td.cleanup)
        reset = getattr(svc, "reset_runtime_state", None)
        if callable(reset):
            reset()

    def test_capture_saves_png_from_selenium_like_driver(self):
        job = db.create_job("outlook", batch_id="shot-1")
        svc._activate_job(int(job["id"]))
        self.addCleanup(lambda: svc._deactivate_job(int(job["id"])))
        driver = MagicMock()
        driver.get_screenshot_as_png.return_value = PNG_BYTES
        driver.page = None

        path = failure_screenshot.capture_registration_failure(driver)

        self.assertTrue(path)
        saved = Path(path)
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), PNG_BYTES)
        self.assertEqual(saved.suffix, ".png")
        loaded = db.get_job(int(job["id"]))
        self.assertEqual(loaded.get("screenshot_path"), str(saved))

    def test_capture_saves_png_from_playwright_page(self):
        job = db.create_job("outlook", batch_id="shot-2")
        svc._activate_job(int(job["id"]))
        self.addCleanup(lambda: svc._deactivate_job(int(job["id"])))
        page = MagicMock(spec=["screenshot"])
        page.screenshot.return_value = PNG_BYTES

        path = failure_screenshot.capture_registration_failure(page)

        self.assertTrue(path)
        self.assertEqual(Path(path).read_bytes(), PNG_BYTES)
        page.screenshot.assert_called()

    def test_capture_does_not_raise_when_browser_is_gone(self):
        job = db.create_job("outlook", batch_id="shot-3")
        svc._activate_job(int(job["id"]))
        self.addCleanup(lambda: svc._deactivate_job(int(job["id"])))
        driver = MagicMock()
        driver.get_screenshot_as_png.side_effect = RuntimeError("session deleted")
        driver.page = None

        path = failure_screenshot.capture_registration_failure(driver)
        self.assertIsNone(path)
        self.assertFalse(db.get_job(int(job["id"])).get("screenshot_path"))

    def test_delete_job_removes_screenshot_file(self):
        job = db.create_job("outlook", batch_id="shot-4")
        svc._activate_job(int(job["id"]))
        self.addCleanup(lambda: svc._deactivate_job(int(job["id"])))
        driver = MagicMock()
        driver.get_screenshot_as_png.return_value = PNG_BYTES
        driver.page = None
        path = Path(failure_screenshot.capture_registration_failure(driver))
        self.assertTrue(path.exists())
        db.update_job(int(job["id"]), status="failed", error="boom")

        self.assertTrue(db.delete_job(int(job["id"]), delete_log=True))
        self.assertFalse(path.exists())

    def test_api_serves_failure_screenshot(self):
        job = db.create_job("outlook", batch_id="shot-5")
        svc._activate_job(int(job["id"]))
        self.addCleanup(lambda: svc._deactivate_job(int(job["id"])))
        driver = MagicMock()
        driver.get_screenshot_as_png.return_value = PNG_BYTES
        driver.page = None
        failure_screenshot.capture_registration_failure(driver)
        db.update_job(int(job["id"]), status="failed", error="页面超时")

        client = create_app(auth_code="test-auth").test_client()
        response = client.get(
            f"/api/jobs/{job['id']}/screenshot",
            headers={"Accept-Encoding": "identity", "X-Auth-Code": "test-auth"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/png", response.content_type)
        self.assertEqual(response.get_data(), PNG_BYTES)

        listed = client.get(
            "/api/jobs?paged=1&page=1&page_size=20",
            headers={"Accept-Encoding": "identity", "X-Auth-Code": "test-auth"},
        ).get_json()
        item = next(row for row in listed["items"] if int(row["id"]) == int(job["id"]))
        self.assertTrue(item.get("has_screenshot"))

    def test_log_api_includes_screenshot_path(self):
        job = db.create_job("outlook", batch_id="shot-6")
        svc._activate_job(int(job["id"]))
        self.addCleanup(lambda: svc._deactivate_job(int(job["id"])))
        driver = MagicMock()
        driver.get_screenshot_as_png.return_value = PNG_BYTES
        driver.page = None
        path = failure_screenshot.capture_registration_failure(driver)

        client = create_app(auth_code="test-auth").test_client()
        response = client.get(
            f"/api/jobs/{job['id']}/log",
            headers={"Accept-Encoding": "identity", "X-Auth-Code": "test-auth"},
        )
        payload = response.get_json()
        self.assertEqual(payload["job"]["screenshot_path"], path)

    def test_modern_ui_has_screenshot_viewer(self):
        html = Path(__file__).resolve().parents[1].joinpath("webui/templates/index.html").read_text(encoding="utf-8")
        self.assertIn('id="shotPanel"', html)
        self.assertIn("查看截图", html)
        self.assertIn("/api/jobs/", html)
        self.assertIn("screenshot", html)


if __name__ == "__main__":
    unittest.main()
