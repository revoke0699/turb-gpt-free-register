# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch

from core.turnstile import (
    CLICK_OFFSET_X,
    TOKEN_MIN_LENGTH,
    CloudflareChallengeError,
    click_turnstile,
    cloudflare_interstitial,
    ensure_turnstile,
    should_retry_cf,
    sync_turnstile,
    token_ready,
)


class _Frame:
    def __init__(self, url, height=80, click_error=None, text="", checked=False):
        self.url = url
        self.clicks = []
        self.click_error = click_error
        self.height = height
        self.text = text
        self.checked = checked

    def evaluate(self, script):
        script = str(script)
        if "checkbox" in script:
            return {"text": self.text, "checked": self.checked}
        if "innerText" in script:
            return self.text
        return {"w": 300, "h": self.height}

    def click(self, selector, position=None, timeout=None):
        if self.click_error:
            raise self.click_error
        self.clicks.append((selector, position, timeout))


class _Locator:
    def __init__(self, count=0, value="", box=None):
        self._count = count
        self._value = value
        self._box = box
        self.first = self

    def count(self):
        return self._count

    def input_value(self, timeout=None):
        return self._value

    def bounding_box(self, timeout=None):
        return self._box


class _Mouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _Page:
    def __init__(self, frames=None, locators=None, title="", url="https://auth.openai.com/log-in"):
        self.frames = frames or []
        self.mouse = _Mouse()
        self.locators = locators or {}
        self._title = title
        self.url = url
        self.evaluated = []
        self.context = MagicMock()
        self.context.cookies.return_value = []

    def locator(self, selector):
        return self.locators.get(selector, _Locator())

    def title(self):
        return self._title

    def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        return TOKEN_MIN_LENGTH if arg else ""


class TurnstileTests(unittest.TestCase):
    def test_token_ready_threshold(self):
        self.assertFalse(token_ready("a" * (TOKEN_MIN_LENGTH - 1)))
        self.assertTrue(token_ready("b" * TOKEN_MIN_LENGTH))

    def test_should_retry_waits_then_spaces_full_sync(self):
        self.assertFalse(should_retry_cf(None, 0, now=10))
        self.assertFalse(should_retry_cf(0, 0, now=2.9))
        self.assertTrue(should_retry_cf(0, 0, now=3))
        self.assertFalse(should_retry_cf(0, 3, now=17.9))
        self.assertTrue(should_retry_cf(0, 3, now=18))

    def test_no_widget_skips_click(self):
        page = _Page()
        self.assertEqual(ensure_turnstile(page, timeout=5), "")
        self.assertEqual(page.mouse.clicks, [])
        self.assertEqual(page.evaluated, [])

    def test_frame_click_uses_checkbox_offset(self):
        frame = _Frame("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile")
        page = _Page(frames=[frame])
        self.assertTrue(click_turnstile(page))
        self.assertEqual(frame.clicks[0][0], "body")
        self.assertEqual(frame.clicks[0][1], {"x": CLICK_OFFSET_X, "y": 40})

    def test_page_click_when_frame_click_fails(self):
        frame = _Frame("https://challenges.cloudflare.com/turnstile/x", click_error=RuntimeError("csp"))
        iframe = _Locator(count=1, box={"x": 10, "y": 20, "width": 300, "height": 65})
        from core.turnstile import IFRAME_SELECTOR
        page = _Page(frames=[frame], locators={IFRAME_SELECTOR: iframe})
        self.assertTrue(click_turnstile(page))
        self.assertEqual(page.mouse.clicks, [(34, 52.5)])

    def test_existing_token_does_not_click(self):
        from core.turnstile import TOKEN_INPUT_SELECTOR
        token = "t" * TOKEN_MIN_LENGTH
        page = _Page(
            frames=[_Frame("https://challenges.cloudflare.com/turnstile")],
            locators={TOKEN_INPUT_SELECTOR: _Locator(count=1, value=token)},
        )
        self.assertEqual(ensure_turnstile(page, timeout=5), token)
        self.assertEqual(page.frames[0].clicks, [])

    def test_interstitial_without_token_raises(self):
        page = _Page(title="Just a moment...", url="https://chatgpt.com/cdn-cgi/challenge-platform/")
        with patch("core.turnstile.time.sleep"):
            with self.assertRaises(CloudflareChallengeError):
                sync_turnstile(page, timeout=0.01)

    def test_security_verification_title_is_interstitial(self):
        page = _Page(title="Performing security verification", url="https://auth.openai.com/")
        self.assertTrue(cloudflare_interstitial(page))

    def test_german_security_verification_title_is_interstitial(self):
        page = _Page(title="Sicherheitsüberprüfung wird durchgeführt", url="https://auth.openai.com/email-verification")
        self.assertTrue(cloudflare_interstitial(page))

    def test_verifying_spinner_is_not_clicked(self):
        frame = _Frame(
            "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile",
            text="Verifying...\nCloudflare\nPrivacy",
        )
        page = _Page(frames=[frame], url="https://chatgpt.com/auth/login")
        with patch("core.turnstile.time.sleep"):
            with self.assertRaises(CloudflareChallengeError):
                sync_turnstile(page, timeout=0.2)
        self.assertEqual(frame.clicks, [])

    def test_german_verifying_spinner_is_not_clicked(self):
        frame = _Frame(
            "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile",
            text="Verifiziere...\nCloudflare",
        )
        page = _Page(frames=[frame], url="https://chatgpt.com/")
        self.assertFalse(click_turnstile(page))
        self.assertEqual(frame.clicks, [])

    def test_checked_human_box_is_not_clicked_again(self):
        frame = _Frame(
            "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile",
            text="Bestätigen Sie, dass Sie ein Mensch sind",
            checked=True,
        )
        page = _Page(frames=[frame], url="https://chatgpt.com/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page")
        self.assertFalse(click_turnstile(page))
        self.assertEqual(frame.clicks, [])

    def test_human_checkbox_is_clicked_only_once(self):
        frame = _Frame(
            "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile",
            text="Bestätigen Sie, dass Sie ein Mensch sind",
        )
        page = _Page(frames=[frame], url="https://chatgpt.com/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page")
        with patch("core.turnstile.time.sleep"):
            with self.assertRaises(CloudflareChallengeError):
                sync_turnstile(page, timeout=0.2)
        self.assertEqual(len(frame.clicks), 1)

    def test_human_checkbox_still_clicks(self):
        frame = _Frame(
            "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile",
            text="Verify you are human",
        )
        page = _Page(frames=[frame])
        self.assertTrue(click_turnstile(page))
        self.assertEqual(frame.clicks[0][0], "body")

    def test_security_verification_page_clicks_checkbox(self):
        frame = _Frame("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/av0/rcv")
        page = _Page(
            frames=[frame],
            title="Performing security verification",
            url="https://auth.openai.com/",
        )
        with patch("core.turnstile.time.sleep"):
            with self.assertRaises(CloudflareChallengeError):
                sync_turnstile(page, timeout=0.01)
        self.assertTrue(frame.clicks)

    def test_selenium_clicks_iframe_from_center(self):
        element = MagicMock()
        element.size = {"width": 300, "height": 65}
        driver = MagicMock()
        driver.page = None
        driver.find_elements.return_value = [element]
        driver.execute_script.return_value = ""
        chains = MagicMock()
        with patch("selenium.webdriver.common.action_chains.ActionChains", return_value=chains) as action_cls:
            self.assertTrue(click_turnstile(driver))
        action_cls.assert_called_once_with(driver)
        chains.move_to_element_with_offset.assert_called_once_with(element, CLICK_OFFSET_X - 150, 0)
        chains.move_to_element_with_offset.return_value.click.assert_called_once_with()
