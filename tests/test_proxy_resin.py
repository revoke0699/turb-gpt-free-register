# -*- coding: utf-8 -*-
import os
import re
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from config import env_loader
from config import proxy as proxy_mod
from core import registration_service as svc
from webui import config_editor


RESIN_TEMPLATE = (
    "http://openai.[task_任务id]:2e17185be7540c60b325d4cbcc0e9780c3647aa40ea53e7af1abdfc896b78045"
    "@8.222.186.217:2260"
)
POOL_PROXY = "socks5://127.0.0.1:7897"


@contextmanager
def _resin_config(*, mode=True, template=RESIN_TEMPLATE, pool=None):
    if pool is None:
        pool = [POOL_PROXY]
    with patch.object(proxy_mod, "PROXY_RESIN_MODE", mode, create=True), \
         patch.object(proxy_mod, "PROXY_RESIN_TEMPLATE", template, create=True), \
         patch.object(proxy_mod, "PROXY_POOL", pool):
        yield


@contextmanager
def _job_id(job_id):
    svc._THREAD_CTX.job_id = job_id
    try:
        yield
    finally:
        try:
            delattr(svc._THREAD_CTX, "job_id")
        except Exception:
            pass


def _task_id_from(url: str, template: str = RESIN_TEMPLATE) -> str:
    prefix, suffix = template.split("[task_任务id]", 1)
    if not url.startswith(prefix) or not url.endswith(suffix):
        raise AssertionError(f"URL 不是由模板展开: {url}")
    return url[len(prefix): len(url) - len(suffix) if suffix else None]


class ProxyResinPickTests(unittest.TestCase):
    def tearDown(self):
        if hasattr(svc._THREAD_CTX, "job_id"):
            delattr(svc._THREAD_CTX, "job_id")

    def test_resin_mode_off_still_uses_proxy_pool(self):
        with _resin_config(mode=False, pool=[POOL_PROXY]):
            self.assertEqual(proxy_mod.pick_proxy(), POOL_PROXY)

    def test_pool_url_with_placeholder_is_expanded_even_when_resin_mode_off(self):
        with _resin_config(mode=False, template="", pool=[RESIN_TEMPLATE]), _job_id(2):
            self.assertEqual(
                proxy_mod.pick_proxy(),
                "http://openai.2:2e17185be7540c60b325d4cbcc0e9780c3647aa40ea53e7af1abdfc896b78045"
                "@8.222.186.217:2260",
            )

    def test_resin_mode_replaces_placeholder_with_job_id(self):
        with _resin_config(), _job_id(12):
            self.assertEqual(
                proxy_mod.pick_proxy(),
                "http://openai.12:2e17185be7540c60b325d4cbcc0e9780c3647aa40ea53e7af1abdfc896b78045"
                "@8.222.186.217:2260",
            )

    def test_resin_mode_same_job_id_stays_sticky(self):
        with _resin_config(), _job_id(12):
            first = proxy_mod.pick_proxy()
            second = proxy_mod.pick_proxy()
        self.assertEqual(first, second)
        self.assertEqual(_task_id_from(first), "12")

    def test_resin_mode_ignores_proxy_pool(self):
        with _resin_config(pool=[POOL_PROXY]), _job_id(1):
            result = proxy_mod.pick_proxy()
        self.assertNotIn("7897", result)
        self.assertEqual(_task_id_from(result), "1")

    def test_resin_mode_without_job_id_generates_unique_ids(self):
        with _resin_config():
            first = proxy_mod.pick_proxy()
            second = proxy_mod.pick_proxy()
        first_id = _task_id_from(first)
        second_id = _task_id_from(second)
        self.assertNotEqual(first_id, second_id)
        self.assertTrue(re.fullmatch(r"[0-9a-f]+", first_id), first_id)
        self.assertTrue(re.fullmatch(r"[0-9a-f]+", second_id), second_id)
        self.assertGreaterEqual(len(first_id), 8)
        self.assertGreaterEqual(len(second_id), 8)

    def test_resin_mode_empty_template_returns_empty_string(self):
        with _resin_config(template=""), _job_id(12), self.assertLogs(proxy_mod.logger, level="WARNING"):
            self.assertEqual(proxy_mod.pick_proxy(), "")

    def test_resin_mode_without_placeholder_returns_template(self):
        raw = "http://openai.static:pass@8.222.186.217:2260"
        with _resin_config(template=raw), _job_id(12), self.assertLogs(proxy_mod.logger, level="WARNING"):
            self.assertEqual(proxy_mod.pick_proxy(), raw)


class ProxyResinConfigTests(unittest.TestCase):
    def test_secret_registry_includes_resin_template(self):
        self.assertIn("PROXY_RESIN_TEMPLATE", env_loader.SECRET_ENV_KEYS)

    def test_webui_exposes_resin_fields(self):
        fields = {item["key"]: item for item in config_editor.EDITABLE_FIELDS}
        self.assertEqual(fields["PROXY_RESIN_MODE"]["type"], "bool")
        self.assertEqual(fields["PROXY_RESIN_MODE"]["group"], "代理池")
        self.assertEqual(fields["PROXY_RESIN_TEMPLATE"]["type"], "str")
        self.assertTrue(fields["PROXY_RESIN_TEMPLATE"].get("secret"))
        self.assertEqual(fields["PROXY_RESIN_TEMPLATE"].get("storage"), "env")
        self.assertEqual(fields["PROXY_RESIN_TEMPLATE"]["group"], "代理池")

    def test_env_overrides_enable_resin_template(self):
        namespace = {
            "PROXY_RESIN_MODE": False,
            "PROXY_RESIN_TEMPLATE": "",
        }
        with patch.dict(os.environ, {
            "PROXY_RESIN_MODE": "True",
            "PROXY_RESIN_TEMPLATE": RESIN_TEMPLATE,
        }, clear=True):
            env_loader.apply_env_overrides(namespace, {
                "PROXY_RESIN_MODE": "bool",
                "PROXY_RESIN_TEMPLATE": "str",
            })
        self.assertTrue(namespace["PROXY_RESIN_MODE"])
        self.assertEqual(namespace["PROXY_RESIN_TEMPLATE"], RESIN_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
