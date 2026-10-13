# -*- coding: utf-8 -*-
"""outlookEmail 自建邮箱服务客户端。

对接 https://github.com/assast/outlookEmail 的项目管理接口：
登录 Web Session 后从项目随机领取邮箱，再读邮件取验证码。
注册成功调用 complete-success，失败调用 complete-failed，未使用则 release 回可领取。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from config import email as _email_cfg
from core.otp_utils import extract_otp, looks_like_openai_email

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
_CALLER_ID = "turb-gpt-free-register"
_CONSUMED_STATUSES = {"used"}
_FAILED_STATUSES = {"failed", "disabled"}


class OutlookEmailError(RuntimeError):
    """outlookEmail 自建服务请求或取码失败。"""


OutlookEmailClientError = OutlookEmailError


@dataclass
class OutlookEmailAccount:
    email: str
    account_id: int
    claim_token: str
    project_key: str
    task_id: str


_CONTEXT_CACHE: dict[str, OutlookEmailAccount] = {}
_CONTEXT_LOCK = threading.RLock()
_SESSION = None
_SESSION_LOCK = threading.RLock()


def _cache_key(email: str) -> str:
    return str(email or "").strip().lower()


def _base_url() -> str:
    raw = str(getattr(_email_cfg, "OUTLOOKEMAIL_API_BASE", "") or "").strip()
    if not raw:
        raise OutlookEmailError("outlookEmail API 地址未配置，请填写自建服务地址（配置 → 邮箱 / OTP）。")
    if not raw.lower().startswith(("http://", "https://")):
        raw = "http://" + raw
    parsed = urlsplit(raw.rstrip("/"))
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise OutlookEmailError("outlookEmail API 地址无效，请填写 http://主机:端口")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")


def _password() -> str:
    value = str(getattr(_email_cfg, "OUTLOOKEMAIL_PASSWORD", "") or "").strip()
    if not value:
        raise OutlookEmailError("outlookEmail 登录密码未配置，请填写自建服务的 Web 登录密码（配置 → 邮箱 / OTP）。")
    return value


def _project_key() -> str:
    value = str(getattr(_email_cfg, "OUTLOOKEMAIL_PROJECT_KEY", "") or "").strip().lower()
    if not value:
        raise OutlookEmailError("outlookEmail 项目标识未配置，请填写项目 project_key（默认 gpt）。")
    return value


def _lease_seconds() -> int:
    try:
        value = int(getattr(_email_cfg, "OUTLOOKEMAIL_LEASE_SECONDS", 1800) or 1800)
    except (TypeError, ValueError):
        value = 1800
    return max(60, min(3600, value))


def _new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _reset_session() -> None:
    global _SESSION
    with _SESSION_LOCK:
        _SESSION = None


def _error_text(payload) -> str:
    if isinstance(payload, dict):
        err = payload.get("error") or payload.get("message")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err)
        if err:
            return str(err)
    text = str(payload or "").strip()
    return text[:240] or "服务端未返回错误信息"


def _parse(resp, path: str):
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OutlookEmailError(f"outlookEmail 响应不是 JSON ({path}): HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        raise OutlookEmailError(f"outlookEmail 请求失败 ({path}): HTTP {resp.status_code}; {_error_text(payload)}")
    if isinstance(payload, dict) and payload.get("success") is False:
        raise OutlookEmailError(f"outlookEmail 请求失败 ({path}): {_error_text(payload)}")
    return payload


def _raw(session: requests.Session, method: str, path: str, *, params=None, json_body=None, headers=None):
    try:
        return session.request(
            method,
            _base_url() + path,
            params=params,
            json=json_body,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            proxies={"http": None, "https": None},
        )
    except requests.RequestException as exc:
        raise OutlookEmailError(f"outlookEmail 请求失败 ({path}): {type(exc).__name__}: {exc}") from exc


def _login(session: requests.Session) -> None:
    resp = _raw(
        session,
        "POST",
        "/login",
        json_body={"password": _password(), "session_duration_days": 30},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    _parse(resp, "/login")


def _ensure_session() -> requests.Session:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            session = _new_session()
            session.trust_env = False
            _login(session)
            _SESSION = session
        return _SESSION


def _csrf_token(session: requests.Session) -> str | None:
    resp = _raw(session, "GET", "/api/csrf-token", headers={"Accept": "application/json"})
    payload = _parse(resp, "/api/csrf-token")
    if not isinstance(payload, dict) or payload.get("csrf_disabled"):
        return None
    token = str(payload.get("csrf_token") or "").strip()
    if not token:
        raise OutlookEmailError("outlookEmail 未返回 CSRF Token")
    return token


def _request(method: str, path: str, *, params=None, json_body=None, write: bool = False, relogin: bool = True):
    session = _ensure_session()
    headers = {"Accept": "application/json"}
    if write:
        headers["Content-Type"] = "application/json"
        token = _csrf_token(session)
        if token:
            headers["X-CSRFToken"] = token
    resp = _raw(session, method, path, params=params, json_body=json_body, headers=headers)
    if resp.status_code in (401, 403) and relogin:
        _reset_session()
        return _request(method, path, params=params, json_body=json_body, write=write, relogin=False)
    return _parse(resp, path)


def _project_path(action: str) -> str:
    return f"/api/projects/{quote(_project_key(), safe='')}/{action}"


def _claim_body(account: OutlookEmailAccount, note: str | None) -> dict:
    return {
        "account_id": account.account_id,
        "claim_token": account.claim_token,
        "caller_id": _CALLER_ID,
        "task_id": account.task_id,
        "detail": str(note or "")[:500],
    }


def pick_account() -> OutlookEmailAccount:
    """从项目里随机领取一个可注册邮箱。"""
    _base_url()
    _password()
    project_key = _project_key()
    task_id = uuid.uuid4().hex
    payload = _request(
        "POST",
        _project_path("claim-random"),
        json_body={
            "caller_id": _CALLER_ID,
            "task_id": task_id,
            "lease_seconds": _lease_seconds(),
        },
        write=True,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise OutlookEmailError("outlookEmail 领取邮箱响应缺少 data")
    email = str(data.get("email") or "").strip()
    if not email or "@" not in email:
        raise OutlookEmailError("outlookEmail 领取邮箱响应缺少有效 email")
    try:
        account_id = int(data.get("account_id"))
    except (TypeError, ValueError) as exc:
        raise OutlookEmailError("outlookEmail 领取邮箱响应缺少 account_id") from exc
    claim_token = str(data.get("claim_token") or "").strip()
    if not claim_token:
        raise OutlookEmailError("outlookEmail 领取邮箱响应缺少 claim_token")
    account = OutlookEmailAccount(
        email=email,
        account_id=account_id,
        claim_token=claim_token,
        project_key=str(data.get("project_key") or project_key),
        task_id=task_id,
    )
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE[_cache_key(email)] = account
    logger.info("[outlookEmail] 已领取邮箱: %s project=%s", email, account.project_key)
    return account


def get_email() -> str:
    return pick_account().email


def get_account_context(email: str) -> OutlookEmailAccount | None:
    with _CONTEXT_LOCK:
        return _CONTEXT_CACHE.get(_cache_key(email))


def release_account(email: str, status: str = "available", note: str | None = None) -> None:
    """按注册结果归还项目邮箱。used 标记成功，failed/disabled 标记失败，其余释放。"""
    with _CONTEXT_LOCK:
        account = _CONTEXT_CACHE.get(_cache_key(email))
    if account is None:
        logger.info("[outlookEmail] 没有可归还的领取记录: %s status=%s", email, status)
        return
    normalized = str(status or "available").strip().lower()
    if normalized in _CONSUMED_STATUSES:
        action = "complete-success"
    elif normalized in _FAILED_STATUSES:
        action = "complete-failed"
    else:
        action = "release"
    try:
        _request("POST", _project_path(action), json_body=_claim_body(account, note), write=True)
    except OutlookEmailError as exc:
        logger.warning("[outlookEmail] 归还邮箱失败: %s action=%s error=%s", email, action, exc)
        return
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE.pop(_cache_key(email), None)
    logger.info("[outlookEmail] 已归还邮箱: %s action=%s note=%s", email, action, note or "")


def _timestamp(item: dict) -> float | None:
    raw = item.get("date") or item.get("received_at") or item.get("created_at")
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value / 1000.0 if value > 10_000_000_000 else value
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _message_item(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "from": item.get("from") or "",
        "subject": item.get("subject") or "",
        "text": item.get("text") or item.get("body") or item.get("body_preview") or "",
        "html": item.get("html") or "",
        "date": item.get("date"),
        "folder": item.get("folder") or "inbox",
    }


def _load_messages(email: str) -> list[dict]:
    encoded = quote(str(email or "").strip(), safe="")
    payload = _request("GET", f"/api/emails/{encoded}", params={"folder": "all", "top": 10})
    emails = payload.get("emails") if isinstance(payload, dict) else None
    if not isinstance(emails, list):
        raise OutlookEmailError("outlookEmail 邮件列表响应缺少 emails")
    return [item for item in emails if isinstance(item, dict)]


def _load_detail(email: str, item: dict) -> dict:
    message_id = str(item.get("id") or "").strip()
    if not message_id:
        return item
    encoded_email = quote(str(email or "").strip(), safe="")
    encoded_id = quote(message_id, safe="")
    folder = str(item.get("folder") or "inbox")
    payload = _request(
        "GET",
        f"/api/email/{encoded_email}/{encoded_id}",
        params={"folder": folder},
    )
    detail = payload.get("email") if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        return item
    merged = dict(item)
    merged.update(detail)
    return merged


def fetch_latest_otp(
    email: str,
    after_ts: float | None = None,
    max_wait: int | None = None,
    poll_interval: int | None = None,
    settle_seconds: int | None = None,
) -> str:
    """轮询 outlookEmail，返回领取时间之后最新的 OpenAI 六位验证码。"""
    target = str(email or "").strip()
    if not target:
        raise OutlookEmailError("outlookEmail 取码缺少邮箱地址")
    wait_seconds = int(max_wait if max_wait is not None else _email_cfg.OTP_MAX_WAIT)
    interval = max(1, int(poll_interval if poll_interval is not None else _email_cfg.OTP_POLL_INTERVAL))
    settle = max(0, int(settle_seconds if settle_seconds is not None else _email_cfg.OTP_SETTLE_SECONDS))
    deadline = time.monotonic() + max(0, wait_seconds)
    best_otp: str | None = None
    best_timestamp = float("-inf")
    settle_until: float | None = None
    last_error = "收件箱为空或尚未出现新的 OpenAI 验证码"

    logger.info("[outlookEmail] 开始轮询邮箱 %s，最长 %ss", target, wait_seconds)
    while time.monotonic() <= deadline:
        try:
            messages = _load_messages(target)
            for mail in sorted(messages, key=lambda item: _timestamp(item) or float("-inf"), reverse=True):
                message_time = _timestamp(mail)
                if after_ts is not None and message_time is not None and message_time < after_ts - 30:
                    continue
                item = _message_item(mail)
                if not looks_like_openai_email(item):
                    continue
                otp = extract_otp(item) or ""
                if not otp:
                    item = _message_item(_load_detail(target, mail))
                    if not looks_like_openai_email(item):
                        continue
                    otp = extract_otp(item) or ""
                if not otp:
                    continue
                candidate_time = float("-inf") if message_time is None else message_time
                if best_otp is None or candidate_time > best_timestamp or (candidate_time == best_timestamp and otp != best_otp):
                    best_otp = otp
                    best_timestamp = candidate_time
                    settle_until = time.monotonic() + settle
                    logger.info("[outlookEmail] 锁定 OTP 候选，等待 %ss 确认", settle)
            now = time.monotonic()
            if best_otp and settle_until is not None and now >= settle_until:
                return best_otp
        except OutlookEmailError as exc:
            last_error = str(exc)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))

    if best_otp:
        return best_otp
    raise OutlookEmailError(f"等待 outlookEmail 验证码超时: {target}; {last_error}")
