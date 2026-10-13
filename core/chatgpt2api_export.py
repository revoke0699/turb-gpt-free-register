# -*- coding: utf-8 -*-
"""把注册成功的 ChatGPT 账号推到 chatgpt2api 号池。"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import requests

from config import chatgpt2api as _cfg

logger = logging.getLogger(__name__)


def _result(
    *,
    status: str,
    ok: bool = False,
    http_status: int | None = None,
    added: int = 0,
    skipped: int = 0,
    refreshed: int = 0,
    message: str = "",
    url: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": ok,
        "http_status": http_status,
        "added": added,
        "skipped": skipped,
        "refreshed": refreshed,
        "message": message,
        "url": url,
    }


def _account_extra(row: dict | None) -> dict:
    row = row or {}
    extra_raw = row.get("extra_json")
    extra: dict = {}
    if isinstance(extra_raw, str) and extra_raw.strip():
        try:
            parsed = json.loads(extra_raw)
            if isinstance(parsed, dict):
                extra = parsed
        except Exception:
            extra = {}
    elif isinstance(extra_raw, dict):
        extra = dict(extra_raw)
    nested = row.get("extra")
    if isinstance(nested, dict):
        extra = {**extra, **nested}
    return extra


def _normalize_plan_type(row: dict, extra: dict) -> str:
    account = extra.get("account") if isinstance(extra.get("account"), dict) else {}
    raw = (
        row.get("current_plan_type")
        or row.get("plan_type")
        or account.get("planType")
        or account.get("plan_type")
        or ""
    )
    text = str(raw or "").strip().lower()
    if not text:
        return "free"
    if "pro" in text:
        return "pro"
    if "team" in text:
        return "team"
    if "plus" in text:
        return "plus"
    if text in {"go", "chatgpt go"} or " go" in f" {text}":
        return "go"
    if "free" in text:
        return "free"
    return text[:32]


_PROXY_NODE_SCHEMES = {"http", "https", "socks5", "socks5h"}


def _chatgpt2api_proxy_node(value: object) -> str:
    """返回 chatgpt2api 能收下的代理节点 URL。

    号池把账号上的 proxy 当成自定义出口，非法地址会让整次导入返回 400。
    环境标签（skyvern:jp）和带路径的地址留空，账号仍可上传。
    """
    raw = str(value or "").strip()
    if not raw or any(char.isspace() for char in raw) or "://" not in raw:
        return ""
    try:
        parsed = urlparse(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme or "").lower() not in _PROXY_NODE_SCHEMES:
        return ""
    if not host or parsed.netloc.endswith(":") or port == 0:
        return ""
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return ""
    return raw


def _upload_original_proxy_enabled() -> bool:
    return bool(getattr(_cfg, "CHATGPT2API_UPLOAD_PROXY", False))


def _account_proxy_node(row: dict, extra: dict) -> str:
    """账号注册时实际使用的原始代理地址。

    优先 proxy_used（注册落库的那条），再回退 extra / 行内 proxy。
    只接受号池能当出口的节点 URL，环境标签留空，避免整次导入 400。
    """
    candidates = (
        row.get("proxy_used"),
        extra.get("proxy") if isinstance(extra.get("proxy"), str) else "",
        row.get("proxy"),
    )
    for candidate in candidates:
        node = _chatgpt2api_proxy_node(candidate)
        if node:
            return node
    return ""


def chatgpt2api_accounts_url(base: str) -> str:
    text = str(base or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/api/accounts"):
        return text
    return f"{text}/api/accounts"


def build_chatgpt2api_account_payload(row: dict | None) -> dict | None:
    row = row or {}
    access_token = str(row.get("access_token") or row.get("accessToken") or "").strip()
    if not access_token:
        return None
    extra = _account_extra(row)
    password = str(
        extra.get("registration_password")
        or row.get("registration_password")
        or ""
    ).strip()
    email = str(row.get("email") or "").strip()
    source_type = str(getattr(_cfg, "CHATGPT2API_SOURCE_TYPE", "web") or "web").strip() or "web"
    payload: dict[str, Any] = {
        "access_token": access_token,
        "type": _normalize_plan_type(row, extra),
        "source_type": source_type,
    }
    if email:
        payload["email"] = email
    if password:
        payload["password"] = password
    if _upload_original_proxy_enabled():
        proxy = _account_proxy_node(row, extra)
        if proxy:
            payload["proxy"] = proxy
    return payload


def export_account_to_chatgpt2api(
    row: dict | None,
    *,
    require_auto_export: bool = True,
) -> dict[str, Any]:
    """把单个账号 POST 到 chatgpt2api /api/accounts。失败不抛给注册主流程。"""
    if require_auto_export and not bool(getattr(_cfg, "CHATGPT2API_AUTO_EXPORT", True)):
        return _result(status="skipped", message="CHATGPT2API_AUTO_EXPORT=False")

    base = str(getattr(_cfg, "CHATGPT2API_API_BASE", "") or "").strip()
    if not base:
        return _result(status="skipped", message="CHATGPT2API_API_BASE 为空")

    auth_key = str(getattr(_cfg, "CHATGPT2API_AUTH_KEY", "") or "").strip()
    if not auth_key:
        return _result(status="skipped", message="CHATGPT2API_AUTH_KEY 为空")

    payload = build_chatgpt2api_account_payload(row)
    if not payload:
        return _result(status="skipped", message="access_token 为空")

    url = chatgpt2api_accounts_url(base)
    timeout = float(getattr(_cfg, "CHATGPT2API_API_TIMEOUT", 20) or 20)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_key}",
        "User-Agent": "turb-gpt-free-register/chatgpt2api",
    }
    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"accounts": [payload]},
            timeout=timeout,
        )
    except Exception as exc:
        return _result(status="failed", message=f"{type(exc).__name__}: {exc}", url=url)

    body: dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = {}
    preview = (resp.text or "")[:300]
    added = int(body.get("added") or 0)
    skipped = int(body.get("skipped") or 0)
    refreshed = int(body.get("refreshed") or 0)
    if resp.status_code == 200:
        return _result(
            status="success",
            ok=True,
            http_status=resp.status_code,
            added=added,
            skipped=skipped,
            refreshed=refreshed,
            message=preview,
            url=url,
        )
    error = ""
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    if isinstance(detail, dict):
        error = str(detail.get("error") or "")
    error = error or str(body.get("error") or preview or f"HTTP {resp.status_code}")
    return _result(
        status="failed",
        http_status=resp.status_code,
        added=added,
        skipped=skipped,
        refreshed=refreshed,
        message=error,
        url=url,
    )


def export_and_record_account(
    account_id: int,
    *,
    require_auto_export: bool = True,
) -> dict[str, Any]:
    from core import db

    row = db.get_account(account_id)
    if not row:
        return _result(status="skipped", message="账号不存在")
    result = export_account_to_chatgpt2api(row, require_auto_export=require_auto_export)
    if result.get("status") != "skipped":
        try:
            db.update_account_chatgpt2api(account_id, result)
        except Exception:
            logger.exception("更新 chatgpt2api 上传状态失败: account_id=%s", account_id)
    return result
