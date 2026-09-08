# -*- coding: utf-8 -*-
"""Cloak/Chromix 必须能走完 Roxy 同款设置密码流程。

Playwright 不能把嵌套 DOM 节点 JSON 序列化；Roxy 的
`_fill_password_page_if_present` / `_click_continue_with_password_if_present`
会从 execute_script 拿回 `{ok, input, button}`。适配层必须把这些节点还原成
CloakElement，否则设置密码会被静默跳过。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloakbrowser_driver import CloakElement, CloakSeleniumDriver
from core.roxy_registration import (
    _click_continue_with_password_if_present,
    _fill_password_page_if_present,
)


class _FakeElementHandle:
    def __init__(self, name="el"):
        self.name = name
        self.disposed = False

    def as_element(self):
        return self

    def evaluate(self, expression, arg=None):
        return None

    def evaluate_handle(self, expression, arg=None):
        return self

    def click(self, timeout=10000):
        return None

    def fill(self, text, timeout=10000):
        return None

    def get_attribute(self, name):
        return None

    def dispose(self):
        self.disposed = True


class _FakeJSHandle:
    """模拟 Playwright JSHandle：根对象含 DOM 节点时 json_value 会失败。"""

    def __init__(self, payload):
        self.payload = payload
        self.disposed = False

    def as_element(self):
        if isinstance(self.payload, _FakeElementHandle):
            return self.payload
        return None

    def json_value(self):
        if _payload_has_element(self.payload):
            raise Exception("jsHandle.jsonValue: Target value cannot be converted to JSON")
        if self.payload is None:
            return None
        return self.payload

    def evaluate(self, expression, arg=None):
        value = self.payload
        if value is None:
            return {"t": "null"}
        if isinstance(value, _FakeElementHandle):
            return {"t": "node"}
        if isinstance(value, list):
            return {"t": "array", "n": len(value)}
        if isinstance(value, dict):
            return {"t": "object", "keys": list(value.keys())}
        return {"t": "json"}

    def evaluate_handle(self, expression, arg=None):
        if isinstance(self.payload, list):
            return _FakeJSHandle(self.payload[arg])
        if isinstance(self.payload, dict):
            return _FakeJSHandle(self.payload[arg])
        raise AssertionError(f"unexpected evaluate_handle payload={self.payload!r} arg={arg!r}")

    def dispose(self):
        self.disposed = True


def _payload_has_element(payload) -> bool:
    if isinstance(payload, _FakeElementHandle):
        return True
    if isinstance(payload, dict):
        return any(_payload_has_element(v) for v in payload.values())
    if isinstance(payload, list):
        return any(_payload_has_element(v) for v in payload)
    return False


class _RecordingElement:
    def __init__(self, name="el"):
        self.name = name
        self.calls = []

    def click(self):
        self.calls.append("click")

    def clear(self):
        self.calls.append("clear")

    def send_keys(self, *args):
        self.calls.append(("send_keys", tuple(str(a) for a in args)))

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class UnwrapJsResultTests(unittest.TestCase):
    def test_nested_dom_nodes_become_cloak_elements(self):
        password_input = _FakeElementHandle("password")
        submit_button = _FakeElementHandle("submit")
        handle = _FakeJSHandle({
            "ok": True,
            "reason": "password_targets",
            "input": password_input,
            "button": submit_button,
        })
        result = CloakSeleniumDriver._unwrap_js_result(object(), handle)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "password_targets")
        self.assertIsInstance(result["input"], CloakElement)
        self.assertIsInstance(result["button"], CloakElement)
        self.assertIs(result["input"].handle, password_input)
        self.assertIs(result["button"].handle, submit_button)
        self.assertTrue(handle.disposed)
        self.assertFalse(password_input.disposed)
        self.assertFalse(submit_button.disposed)

    def test_plain_json_result_still_unwraps(self):
        handle = _FakeJSHandle({"ok": False, "reason": "missing_password_input", "url": "https://auth.openai.com/"})
        result = CloakSeleniumDriver._unwrap_js_result(object(), handle)
        self.assertEqual(result, {"ok": False, "reason": "missing_password_input", "url": "https://auth.openai.com/"})
        self.assertTrue(handle.disposed)

    def test_root_element_becomes_cloak_element_and_is_not_disposed(self):
        element = _FakeElementHandle("email")
        handle = _FakeJSHandle(element)
        result = CloakSeleniumDriver._unwrap_js_result(object(), handle)
        self.assertIsInstance(result, CloakElement)
        self.assertIs(result.handle, element)
        self.assertFalse(element.disposed)


class ElementLikeTests(unittest.TestCase):
    def test_cloak_element_and_recording_element_are_element_like(self):
        from core.roxy_registration import _is_element_like

        el = CloakElement(page=object(), handle=_FakeElementHandle())
        self.assertTrue(_is_element_like(el))
        self.assertTrue(_is_element_like(_RecordingElement()))
        self.assertFalse(_is_element_like({}))
        self.assertFalse(_is_element_like(None))
        self.assertFalse(_is_element_like({"ok": True}))


class ContinueWithPasswordTests(unittest.TestCase):
    def test_clicks_button_when_adapter_returns_element(self):
        button = _RecordingElement("continue")

        class Driver:
            current_url = "https://auth.openai.com/email-verification"

            def execute_script(self, script, *args):
                if "continuewithpassword" in script or "create-account/password" in script:
                    return {
                        "ok": True,
                        "reason": "continue_with_password_target",
                        "button": button,
                        "href": "/create-account/password",
                        "text": "使用密码继续",
                    }
                return None

        with patch("core.roxy_registration._human_click") as click, \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False):
            result = _click_continue_with_password_if_present(Driver())
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("reason"), "clicked_continue_with_password")
        click.assert_called_once()
        self.assertIs(click.call_args.args[1], button)

    def test_falls_back_to_in_page_click_when_button_is_empty_dict(self):
        class Driver:
            current_url = "https://auth.openai.com/email-verification"

            def __init__(self):
                self.scripts = []

            def execute_script(self, script, *args):
                self.scripts.append(script)
                if "clicked_continue_with_password_in_page" in script or "btn.click()" in script and "isPasswordCreate" in script and "button: btn" not in script:
                    return {"ok": True, "reason": "clicked_continue_with_password_in_page", "href": "/create-account/password"}
                if "continuewithpassword" in script:
                    return {
                        "ok": True,
                        "reason": "continue_with_password_target",
                        "button": {},
                        "href": "/create-account/password",
                        "text": "使用密码继续",
                    }
                return {"ok": False, "reason": "unexpected"}

        driver = Driver()
        result = _click_continue_with_password_if_present(driver)
        self.assertTrue(result.get("ok"), result)
        self.assertIn("clicked", str(result.get("reason") or ""))
        self.assertGreaterEqual(len(driver.scripts), 2)


class FillPasswordPageTests(unittest.TestCase):
    def test_sets_password_when_email_otp_offers_continue_with_password(self):
        password_input = _RecordingElement("password")
        submit_button = _RecordingElement("submit")

        class Driver:
            def __init__(self):
                self.phase = "otp"
                self.current_url = "https://auth.openai.com/email-verification"

            def execute_script(self, script, *args):
                s = script or ""
                if "one-time-code" in s and "buttons" in s:
                    if self.phase in ("otp", "done"):
                        return {
                            "url": self.current_url,
                            "inputs": [{"autocomplete": "one-time-code", "name": "code"}],
                            "buttons": [],
                            "errors": [],
                            "text": "",
                        }
                    return {"url": self.current_url, "inputs": [], "buttons": [], "errors": [], "text": ""}
                if "continuewithpassword" in s or ("create-account/password" in s and "isPasswordCreate" in s):
                    if self.phase == "otp":
                        self.phase = "password"
                        self.current_url = "https://auth.openai.com/create-account/password"
                        return {
                            "ok": True,
                            "reason": "continue_with_password_target",
                            "button": _RecordingElement("continue"),
                            "href": "/create-account/password",
                            "text": "Continue with password",
                        }
                    return {"ok": False, "reason": "missing_continue_with_password"}
                if "password_targets" in s or 'input[type="password"]' in s and "missing_password_input" in s:
                    return {
                        "ok": True,
                        "reason": "password_targets",
                        "input": password_input,
                        "button": submit_button,
                    }
                if "enabled_submit_target" in s or "missing_enabled_submit" in s:
                    self.phase = "done"
                    self.current_url = "https://auth.openai.com/email-verification"
                    return {
                        "ok": True,
                        "reason": "enabled_submit_target",
                        "button": submit_button,
                        "text": "Continue",
                        "type": "submit",
                        "dd": "Continue",
                        "ariaDisabled": "",
                    }
                if "autocomplete" in s and "inputs" in s and "forms" in s:
                    return {
                        "url": self.current_url,
                        "inputs": [{"type": "password", "name": "password", "visible": True, "autocomplete": "new-password"}],
                        "forms": [{"action": ""}],
                        "buttons": [{"type": "submit", "visible": True, "disabled": False}],
                    }
                return {}

            def execute_cdp_cmd(self, cmd, params=None):
                return None

        with patch("core.roxy_registration.human_delay"), \
             patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False), \
             patch("core.roxy_registration._registration_password", return_value="Aa1!Bb2@Cc3#"), \
             patch("core.roxy_registration._has_access_token", return_value=False), \
             patch("core.roxy_registration._human_click"), \
             patch("core.roxy_registration._human_type_text") as type_text:
            password = _fill_password_page_if_present(Driver(), "user@example.com", timeout=3)

        self.assertEqual(password, "Aa1!Bb2@Cc3#")
        type_text.assert_called()
        self.assertEqual(type_text.call_args.args[1], password_input)
        self.assertEqual(type_text.call_args.args[2], "Aa1!Bb2@Cc3#")

    def test_cloak_registration_still_calls_shared_password_flow(self):
        src = Path("core/cloakbrowser_registration.py").read_text(encoding="utf-8")
        self.assertIn("_fill_password_page_if_present(driver, email, timeout=25)", src)
        self.assertIn('"registration_password": openai_password', src)


class ContinueWithPasswordMatcherTests(unittest.TestCase):
    def test_matcher_covers_localized_continue_with_password_text(self):
        from core import roxy_registration as roxy

        src = roxy._CONTINUE_WITH_PASSWORD_FINDER_JS
        for needle in (
            "使用密码继续",
            "使用密碼繼續",
            "continuewithpassword",
            "/create-account/password",
        ):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
