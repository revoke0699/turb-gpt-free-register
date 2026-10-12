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
    _click_resend_email_otp,
    _fill_password_page_if_present,
    _is_email_verification_page,
    _type_otp,
    _wait_email_submit_next_state,
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

    def test_cloak_element_text_reads_inner_text_and_swallows_evaluate_errors(self):
        class _Handle:
            def __init__(self, value):
                self.value = value

            def evaluate(self, expression, arg=None):
                if self.value is None:
                    raise RuntimeError("evaluate failed")
                return self.value

        visible = CloakElement(page=object(), handle=_Handle("Resend email"))
        broken = CloakElement(page=object(), handle=_Handle(None))
        self.assertEqual(visible.text, "Resend email")
        self.assertEqual(broken.text, "")


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
                    if self.phase == "done":
                        return {
                            "url": self.current_url,
                            "inputs": [{"type": "text", "name": "code", "visible": True, "autocomplete": "one-time-code"}],
                            "forms": [{"action": ""}],
                            "buttons": [{"type": "submit", "visible": True, "disabled": False}],
                        }
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


class PasswordPageTakesPrecedenceTests(unittest.TestCase):
    """email-verification URL 上如果已经出现密码框，必须走设置密码，而不是去找 OTP。"""

    class _Driver:
        def __init__(self):
            self.current_url = "https://auth.openai.com/email-verification"
            self.phase = "password"

        def execute_script(self, script, *args):
            s = script or ""
            if "one-time-code" in s and "buttons" in s:
                if self.phase == "otp":
                    return {
                        "url": self.current_url,
                        "inputs": [{"autocomplete": "one-time-code", "name": "code"}],
                        "buttons": [],
                        "errors": [],
                        "text": "",
                    }
                return {
                    "url": self.current_url,
                    "inputs": [{"type": "password", "name": "password", "autocomplete": "new-password"}],
                    "buttons": [{"text": "Continue"}],
                    "errors": [],
                    "text": "Create a password",
                }
            if "continuewithpassword" in s or "isPasswordCreate" in s:
                return {"ok": False, "reason": "missing_continue_with_password"}
            if "autocomplete" in s and "inputs" in s and "forms" in s:
                if self.phase == "otp":
                    return {
                        "url": self.current_url,
                        "inputs": [{"type": "text", "name": "code", "visible": True, "autocomplete": "one-time-code"}],
                        "forms": [{"action": ""}],
                        "buttons": [{"type": "submit", "visible": True, "disabled": False}],
                    }
                return {
                    "url": self.current_url,
                    "inputs": [{"type": "password", "name": "password", "visible": True, "autocomplete": "new-password"}],
                    "forms": [{"action": ""}],
                    "buttons": [{"type": "submit", "visible": True, "disabled": False}],
                }
            if "password_targets" in s or ('input[type="password"]' in s and "missing_password_input" in s):
                return {
                    "ok": True,
                    "reason": "password_targets",
                    "input": type("El", (), {"click": lambda self: None, "send_keys": lambda self, *a: None})(),
                    "button": type("El", (), {"click": lambda self: None})(),
                }
            if "enabled_submit_target" in s or "missing_enabled_submit" in s:
                self.phase = "otp"
                return {
                    "ok": True,
                    "reason": "enabled_submit_target",
                    "button": type("El", (), {"click": lambda self: None})(),
                    "text": "Continue",
                    "type": "submit",
                    "dd": "Continue",
                    "ariaDisabled": "",
                }
            return {}

        def execute_cdp_cmd(self, cmd, params=None):
            return None

    def test_email_verification_url_with_password_input_is_not_otp_page(self):
        self.assertFalse(_is_email_verification_page(self._Driver()))

    def test_fill_password_when_verification_url_shows_password_field(self):
        driver = self._Driver()
        with patch("core.roxy_registration.human_delay"), \
             patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False), \
             patch("core.roxy_registration._registration_password", return_value="Aa1!Bb2@Cc3#"), \
             patch("core.roxy_registration._has_access_token", return_value=False), \
             patch("core.roxy_registration._human_click"), \
             patch("core.roxy_registration._human_type_text") as type_text:
            password = _fill_password_page_if_present(driver, "user@example.com", timeout=3)
        self.assertEqual(password, "Aa1!Bb2@Cc3#")
        type_text.assert_called()

    def test_wait_next_state_prefers_password_over_otp_when_password_input_exists(self):
        with patch("core.roxy_registration._has_access_token", return_value=False), \
             patch("core.roxy_registration.time.sleep"):
            state = _wait_email_submit_next_state(self._Driver(), "user@example.com", timeout=1)
        self.assertEqual(state, "password")

    def test_wait_login_js_ready_uses_playwright_wait_for_function(self):
        from core.roxy_registration import _wait_login_js_ready

        class _Page:
            def wait_for_selector(self, selector, timeout=20000):
                self.selector = selector

            def wait_for_function(self, expression, timeout=20000):
                self.expression = expression

        class _Driver:
            def __init__(self):
                self.page = _Page()

        driver = _Driver()
        self.assertTrue(_wait_login_js_ready(driver, timeout=5))
        self.assertIn("email-verification", driver.page.expression)

    def test_submit_treats_email_verification_navigation_as_success(self):
        from core.roxy_registration import _submit_email_form_stable

        class _Page:
            url = "https://auth.openai.com/email-verification"

            def wait_for_function(self, expression, timeout=20000):
                return None

        class _Driver:
            page = _Page()

        result = _submit_email_form_stable(_Driver(), "user@example.com")
        self.assertTrue(result["ok"])
        self.assertIn("otp", result["reason"])

    def test_submit_email_form_stable_uses_playwright_locator_click(self):
        from core.roxy_registration import _submit_email_form_stable

        class _Control:
            def press(self, key):
                self.key = key

            def click(self, timeout=8000, no_wait_after=True):
                self.clicked = True

        class _Locator:
            def __init__(self):
                self._control = _Control()

            @property
            def first(self):
                return self._control

        class _Page:
            url = "https://chatgpt.com/auth/login?email=user%40example.com"

            def locator(self, selector):
                self.selector = selector
                return _Locator()

            def wait_for_selector(self, selector, timeout=20000):
                return None

            def wait_for_function(self, expression, timeout=20000):
                return None

        class _Driver:
            page = _Page()

        result = _submit_email_form_stable(_Driver(), "user@example.com")
        self.assertTrue(result["ok"])
        self.assertIn("submitted", result["reason"])

    def test_spinning_submit_after_enter_is_pending_and_not_clicked(self):
        from core.roxy_registration import _submit_email_form_stable

        class _Clock:
            def __init__(self):
                self.now = 1000.0

            def time(self):
                return self.now

            def sleep(self, seconds):
                self.now += float(seconds)

        class _Email:
            pressed = False

            def press(self, key):
                self.pressed = key == "Enter"

        class _Submit:
            def __init__(self, email):
                self.email = email
                self.clicked = False

            def evaluate(self, expression):
                return self.email.pressed

            def click(self, timeout=5000, no_wait_after=True):
                self.clicked = True
                raise AssertionError("转圈中的提交按钮不应再点击")

        email = _Email()
        submit = _Submit(email)

        class _Page:
            url = "https://chatgpt.com/auth/login"

            def locator(self, selector):
                if "submit" in selector:
                    return type("L", (), {"first": submit})()
                return type("L", (), {"first": email})()

            def wait_for_function(self, expression, timeout=20000):
                return None

        class _Driver:
            page = _Page()

        clock = _Clock()
        with patch("core.roxy_registration.time.time", clock.time), \
             patch("core.roxy_registration.time.sleep", clock.sleep):
            result = _submit_email_form_stable(_Driver(), "user@example.com")
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "enter_pending")
        self.assertFalse(submit.clicked)
        self.assertTrue(email.pressed)

    def test_click_timeout_on_loading_button_counts_as_pending(self):
        from core.roxy_registration import _submit_email_form_stable

        class _Clock:
            def __init__(self):
                self.now = 1000.0

            def time(self):
                return self.now

            def sleep(self, seconds):
                self.now += float(seconds)

        class _Email:
            def press(self, key):
                return None

        class _Submit:
            def __init__(self):
                self.loading = False
                self.clicked = False

            def evaluate(self, expression):
                return self.loading

            def click(self, timeout=5000, no_wait_after=True):
                self.clicked = True
                self.loading = True
                raise TimeoutError(
                    'Locator.click: Timeout 5000ms exceeded.\n'
                    'element is not enabled'
                )

        email = _Email()
        submit = _Submit()

        class _Page:
            url = "https://chatgpt.com/auth/login"

            def locator(self, selector):
                if "submit" in selector:
                    return type("L", (), {"first": submit})()
                return type("L", (), {"first": email})()

            def wait_for_function(self, expression, timeout=20000):
                return None

        class _Driver:
            page = _Page()

        clock = _Clock()
        with patch("core.roxy_registration.time.time", clock.time), \
             patch("core.roxy_registration.time.sleep", clock.sleep):
            result = _submit_email_form_stable(_Driver(), "user@example.com")
        self.assertTrue(submit.clicked)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "enter_pending")

    def test_enabled_submit_still_uses_click(self):
        from core.roxy_registration import _submit_email_form_stable

        class _Clock:
            def __init__(self):
                self.now = 1000.0

            def time(self):
                return self.now

            def sleep(self, seconds):
                self.now += float(seconds)

        class _Email:
            def press(self, key):
                return None

        class _Submit:
            def __init__(self):
                self.clicked = False

            def evaluate(self, expression):
                return False

            def click(self, timeout=5000, no_wait_after=True):
                self.clicked = True

        submit = _Submit()

        class _Page:
            url = "https://chatgpt.com/auth/login"

            def locator(self, selector):
                if "submit" in selector:
                    return type("L", (), {"first": submit})()
                return type("L", (), {"first": _Email()})()

            def wait_for_function(self, expression, timeout=20000):
                return None

        class _Driver:
            page = _Page()

        clock = _Clock()
        with patch("core.roxy_registration.time.time", clock.time), \
             patch("core.roxy_registration.time.sleep", clock.sleep):
            result = _submit_email_form_stable(_Driver(), "user@example.com")
        self.assertTrue(submit.clicked)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "playwright_enter_click")

    def test_submit_step_does_not_treat_loading_form_as_missing_input(self):
        from core.roxy_registration import _submit_email_step

        class _Driver:
            page = object()
            current_url = "https://chatgpt.com/auth/login"

        with patch("core.roxy_registration._stabilize_email_input_before_submit", return_value={"ok": True}), \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False), \
             patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._submit_email_form_stable", return_value={"ok": False, "reason": "TimeoutError: element is not enabled"}), \
             patch("core.roxy_registration._email_form_submit_pending", return_value=True), \
             patch("core.roxy_registration._submit_nearest_form_for_active_input") as nearest:
            _submit_email_step(_Driver(), "user@example.com")
        nearest.assert_not_called()

    def test_wait_next_state_keeps_email_page_when_only_disabled_input_remains(self):
        class _Loc:
            def __init__(self, count):
                self._count = count

            def count(self):
                return self._count

        class _Page:
            url = "https://chatgpt.com/auth/login"

            def locator(self, selector):
                if "password" in selector or "one-time-code" in selector or 'name="code"' in selector:
                    return _Loc(0)
                return _Loc(1)

            def wait_for_url(self, *args, **kwargs):
                raise TimeoutError("still on login")

            def wait_for_load_state(self, *args, **kwargs):
                return None

        class _Driver:
            page = _Page()
            current_url = _Page.url

        with patch("core.roxy_registration.time.sleep"):
            state = _wait_email_submit_next_state(_Driver(), "user@example.com", timeout=0)
        self.assertEqual(state, "email_page")

    def test_wait_next_state_on_playwright_page_skips_evaluate_handle(self):
        class _Loc:
            def count(self):
                return 1

        class _Page:
            url = "https://auth.openai.com/create-account/password"

            def locator(self, selector):
                return _Loc()

            def wait_for_url(self, *args, **kwargs):
                return None

            def wait_for_load_state(self, *args, **kwargs):
                return None

        class _Driver:
            page = _Page()
            current_url = _Page.url

        with patch("core.roxy_registration._email_input_value_state") as state_fn, \
             patch("core.roxy_registration._has_access_token") as token_fn:
            state = _wait_email_submit_next_state(_Driver(), "user@example.com", timeout=1)
        self.assertEqual(state, "password")
        state_fn.assert_not_called()
        token_fn.assert_not_called()

    def test_playwright_stuck_login_email_presses_enter_without_page_js(self):
        presses = []

        class _Page:
            def __init__(self):
                self.url = "https://chatgpt.com/auth/login?email=user%40example.com"

            def locator(self, selector):
                page = self

                class _Box:
                    first = None

                    def count(self):
                        raise AssertionError(f"locator.count must not run after submit: {selector}")

                    def press(self, key, timeout=None):
                        presses.append(key)
                        page.url = "https://auth.openai.com/email-verification"

                box = _Box()
                box.first = box
                return box

            def wait_for_url(self, *args, **kwargs):
                raise TimeoutError("stuck")

            def wait_for_load_state(self, *args, **kwargs):
                return None

        class _Driver:
            def __init__(self):
                self.page = _Page()

            @property
            def current_url(self):
                return self.page.url

        clock = {"t": 1000.0}

        def fake_time():
            return clock["t"]

        def fake_sleep(seconds):
            clock["t"] += float(seconds)

        with patch("core.roxy_registration.time.time", fake_time), \
             patch("core.roxy_registration.time.sleep", fake_sleep), \
             patch("core.roxy_registration._email_input_value_state") as state_fn, \
             patch("core.roxy_registration._settle_cloudflare") as settle:
            state = _wait_email_submit_next_state(_Driver(), "user@example.com", timeout=8)
        self.assertEqual(presses, ["Enter"])
        self.assertEqual(state, "otp")
        state_fn.assert_not_called()
        settle.assert_not_called()

    def test_wait_next_state_clicks_auth_security_verification(self):
        class _Page:
            def __init__(self):
                self.url = "https://auth.openai.com/"
                self.title_calls = 0

            def title(self):
                self.title_calls += 1
                return getattr(self, "_title", "Performing security verification")

            def wait_for_url(self, *args, **kwargs):
                raise TimeoutError("challenge")

            def wait_for_load_state(self, *args, **kwargs):
                return None

        class _Driver:
            def __init__(self):
                self.page = _Page()

            @property
            def current_url(self):
                return self.page.url

        driver = _Driver()

        def settle(target, timeout=20.0, poke=False):
            self.assertGreater(timeout, 0)
            self.assertFalse(poke)
            target.page.url = "https://auth.openai.com/email-verification"
            target.page._title = "Enter code"
            return "token"

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._email_input_value_state") as state_fn, \
             patch("core.roxy_registration._settle_cloudflare", side_effect=settle):
            state = _wait_email_submit_next_state(driver, "user@example.com", timeout=5)
        self.assertEqual(state, "otp")
        self.assertGreater(driver.page.title_calls, 0)
        state_fn.assert_not_called()

    def test_wait_next_state_clicks_when_challenge_frame_is_present(self):
        class _Frame:
            url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile/if/ov2"

        class _Page:
            def __init__(self):
                self.url = "https://auth.openai.com/api/accounts/authorize"
                self.frames = [_Frame()]

            def title(self):
                return ""

            def wait_for_url(self, *args, **kwargs):
                raise TimeoutError("challenge")

            def wait_for_load_state(self, *args, **kwargs):
                return None

        driver_page = _Page()

        class _Driver:
            page = driver_page

            @property
            def current_url(self):
                return driver_page.url

        def settle(target, timeout=20.0, poke=False):
            target.page.frames = []
            target.page.url = "https://auth.openai.com/create-account/password"
            return "token"

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._settle_cloudflare", side_effect=settle):
            state = _wait_email_submit_next_state(_Driver(), "user@example.com", timeout=5)
        self.assertEqual(state, "password")

    def test_wait_next_state_does_not_treat_challenge_as_otp(self):
        class _Frame:
            url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile/if/ov2"

        class _Page:
            def __init__(self):
                self.url = "https://auth.openai.com/email-verification"
                self.frames = [_Frame()]
                self.cleared = False

            def title(self):
                if self.cleared:
                    return "Create password"
                return "Performing security verification"

            def wait_for_url(self, *args, **kwargs):
                raise TimeoutError("challenge")

            def wait_for_load_state(self, *args, **kwargs):
                return None

        class _Driver:
            def __init__(self):
                self.page = _Page()

            @property
            def current_url(self):
                return self.page.url

        def settle(target, timeout=20.0, poke=False):
            target.page.frames = []
            target.page.cleared = True
            target.page.url = "https://auth.openai.com/create-account/password"
            return "token"

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._settle_cloudflare", side_effect=settle) as settle_mock:
            state = _wait_email_submit_next_state(_Driver(), "user@example.com", timeout=5)
        self.assertEqual(state, "password")
        settle_mock.assert_called()

    def test_type_otp_clicks_german_cloudflare_before_inputs(self):
        class _Frame:
            url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile/if/ov2"

        class _Page:
            def __init__(self):
                self.url = "https://auth.openai.com/email-verification"
                self.frames = [_Frame()]
                self.cleared = False

            def title(self):
                if self.cleared:
                    return "Enter the code"
                return "Sicherheitsüberprüfung wird durchgeführt"

        class _Input:
            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def get_attribute(self, name):
                return {"autocomplete": "one-time-code", "name": "code"}.get(name, "")

        class _Driver:
            def __init__(self):
                self.page = _Page()
                self.current_url = self.page.url

            def find_elements(self, by, selector):
                if not self.page.cleared:
                    return []
                if "one-time-code" in selector:
                    return [_Input()]
                return []

            def execute_script(self, script, *args):
                return {"url": self.current_url, "title": "", "inputs": [], "buttons": [], "errors": [], "text": ""}

        driver = _Driver()

        def settle(target, timeout=20.0, poke=False):
            target.page.frames = []
            target.page.cleared = True
            return "token"

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._human_type_text") as type_text, \
             patch("core.roxy_registration._settle_cloudflare", side_effect=settle) as settle_mock:
            _type_otp(driver, "784627")
        settle_mock.assert_called()
        type_text.assert_called_once()
        self.assertEqual(type_text.call_args.args[2], "784627")

    def test_password_flow_clears_cloudflare_on_verification_url(self):
        class _Frame:
            url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile/if/ov2"

        class _Page:
            def __init__(self):
                self.url = "https://auth.openai.com/email-verification"
                self.frames = [_Frame()]
                self.cleared = False

            def title(self):
                if self.cleared:
                    return "Check your inbox"
                return "Sicherheitsüberprüfung wird durchgeführt"

        class _Driver:
            def __init__(self):
                self.page = _Page()
                self.current_url = self.page.url

            def execute_script(self, script, *args):
                if self.page.cleared:
                    return {"ok": False, "reason": "missing_continue_with_password", "url": self.current_url, "inputs": [], "buttons": [], "text": "code"}
                return {"url": self.current_url, "inputs": [], "buttons": [], "errors": [], "text": ""}

        driver = _Driver()

        def settle(target, timeout=20.0, poke=False):
            target.page.frames = []
            target.page.cleared = True
            return "token"

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._settle_cloudflare", side_effect=settle) as settle_mock:
            result = _fill_password_page_if_present(driver, "user@example.com", timeout=3)
        self.assertIsNone(result)
        settle_mock.assert_called()

    def test_password_link_is_polled_before_giving_up(self):
        class _Driver:
            def __init__(self):
                self.current_url = "https://auth.openai.com/email-verification"
                self.calls = 0
                self.phase = "otp"

            def execute_script(self, script, *args):
                s = script or ""
                if "continuewithpassword" in s or "isPasswordCreate" in s:
                    self.calls += 1
                    if self.calls >= 3 and self.phase == "otp":
                        self.phase = "password"
                        self.current_url = "https://auth.openai.com/create-account/password"
                        return {
                            "ok": True,
                            "reason": "clicked_continue_with_password_in_page",
                            "href": "/create-account/password",
                            "text": "Continue with password",
                        }
                    return {"ok": False, "reason": "missing_continue_with_password"}
                if "one-time-code" in s and "buttons" in s:
                    return {
                        "url": self.current_url,
                        "inputs": [{"autocomplete": "one-time-code", "name": "code"}] if self.phase == "done" else [],
                        "buttons": [],
                        "errors": [],
                        "text": "",
                    }
                if "autocomplete" in s and "inputs" in s and "forms" in s:
                    if self.phase == "password":
                        return {
                            "url": self.current_url,
                            "inputs": [{"type": "password", "name": "password", "visible": True, "autocomplete": "new-password"}],
                            "forms": [{"action": ""}],
                            "buttons": [{"type": "submit", "visible": True, "disabled": False}],
                        }
                    if self.phase == "done":
                        return {
                            "url": self.current_url,
                            "inputs": [{"type": "text", "name": "code", "visible": True, "autocomplete": "one-time-code"}],
                            "forms": [{"action": ""}],
                            "buttons": [{"type": "submit", "visible": True, "disabled": False}],
                        }
                    return {
                        "url": self.current_url,
                        "inputs": [],
                        "forms": [{"action": ""}],
                        "buttons": [],
                    }
                if "password_targets" in s or ('input[type="password"]' in s and "missing_password_input" in s):
                    return {
                        "ok": True,
                        "reason": "password_targets",
                        "input": _RecordingElement("password"),
                        "button": _RecordingElement("submit"),
                    }
                if "enabled_submit_target" in s or "missing_enabled_submit" in s:
                    self.phase = "done"
                    self.current_url = "https://auth.openai.com/email-verification"
                    return {
                        "ok": True,
                        "reason": "enabled_submit_target",
                        "button": _RecordingElement("submit"),
                        "text": "Continue",
                        "type": "submit",
                        "dd": "Continue",
                        "ariaDisabled": "",
                    }
                if "passwordless" in s:
                    return {"ok": False, "reason": "missing_passwordless_button"}
                return {}

        driver = _Driver()
        with patch("core.roxy_registration.human_delay"), \
             patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False), \
             patch("core.roxy_registration._registration_password", return_value="Aa1!Bb2@Cc3#"), \
             patch("core.roxy_registration._has_access_token", return_value=False), \
             patch("core.roxy_registration._human_click"), \
             patch("core.roxy_registration._human_type_text"):
            password = _fill_password_page_if_present(driver, "user@example.com", timeout=3)
        self.assertEqual(password, "Aa1!Bb2@Cc3#")
        self.assertGreaterEqual(driver.calls, 3)


class ResendEmailOtpTests(unittest.TestCase):
    def test_resend_clicks_handle_without_text_attribute(self):
        class _Button:
            def get_attribute(self, name):
                return {"value": "resend"}.get(name)

            def click(self):
                return None

        class _Driver:
            def __init__(self):
                self.current_url = "https://auth.openai.com/email-verification"

            def execute_script(self, script, *args):
                if "ariaInvalid" in script or "document.title" in script:
                    return {
                        "url": self.current_url,
                        "title": "Check your inbox - OpenAI",
                        "inputs": [{"name": "code", "autocomplete": "one-time-code"}],
                        "buttons": [{"value": "resend", "text": "Resend email", "disabled": False}],
                        "errors": [],
                        "text": "Check your inbox",
                    }
                return _Button()

        clicked = []

        def _click(driver, el, label=""):
            clicked.append(label)
            self.assertFalse(hasattr(el, "text"))

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._cloudflare_page_visible", return_value=False), \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False), \
             patch("core.roxy_registration._human_click", side_effect=_click):
            result = _click_resend_email_otp(_Driver(), timeout=3)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("text"), "resend")
        self.assertEqual(clicked, ["resend_otp"])

    def test_single_blank_snapshot_does_not_reload(self):
        class _Button:
            def get_attribute(self, name):
                return {"value": "resend"}.get(name)

        class _Driver:
            def __init__(self):
                self.current_url = "https://auth.openai.com/email-verification"
                self.reads = 0
                self.reloads = 0

            def execute_script(self, script, *args):
                if "ariaInvalid" in script or "document.title" in script:
                    self.reads += 1
                    if self.reads == 1:
                        return {
                            "url": self.current_url,
                            "title": "",
                            "inputs": [],
                            "buttons": [],
                            "errors": [],
                            "text": "",
                        }
                    return {
                        "url": self.current_url,
                        "title": "Check your inbox - OpenAI",
                        "inputs": [{"name": "code"}],
                        "buttons": [{"value": "resend", "text": "Resend email"}],
                        "errors": [],
                        "text": "Check your inbox",
                    }
                return _Button()

        driver = _Driver()

        def _reload(target):
            driver.reloads += 1

        with patch("core.roxy_registration.time.sleep"), \
             patch("core.roxy_registration._cloudflare_page_visible", return_value=False), \
             patch("core.roxy_registration._browser_actions_enabled", return_value=False), \
             patch("core.roxy_registration._reload_current_page", side_effect=_reload), \
             patch("core.roxy_registration._human_click"):
            result = _click_resend_email_otp(driver, timeout=3)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(driver.reloads, 0)


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
